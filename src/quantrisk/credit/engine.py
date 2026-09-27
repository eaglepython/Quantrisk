"""Credit module orchestration: develop, calibrate, validate, score and stress."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quantrisk.credit import calibration as cal
from quantrisk.credit.macro import Satellite, fit_satellite, inv_logit, logit, scenario_pd
from quantrisk.credit.pd_model import FEATURES, PDModel, discrimination, fit, information_value, make_features


@dataclass
class CreditOutput:
    model: PDModel
    satellite: Satellite
    central_tendency: float
    dev_metrics: dict[str, float]
    oot_metrics: dict[str, float]
    binomial: pd.DataFrame
    psi: float
    iv: dict[str, float]
    coefficients: dict[str, float]
    obligor_pd: pd.DataFrame                    # current book x scenarios
    grade_summary: pd.DataFrame
    default_rates: pd.Series
    extra: dict[str, Any] = field(default_factory=dict)


def run_credit(obligors: pd.DataFrame, macro: pd.DataFrame, cfg: dict[str, Any],
               macro_scenarios: list[dict[str, Any]]) -> CreditOutput:
    d0, d1 = cfg["dev_years"]
    o0, o1 = cfg["oot_years"]
    cur = int(cfg["current_year"])
    ms: dict[str, float] = cfg["master_scale"]
    grades = list(ms)

    hist = obligors[obligors.default_flag.notna()].copy()
    hist["default_flag"] = hist.default_flag.astype(int)
    dev = hist[(hist.year >= d0) & (hist.year <= d1)]
    oot = hist[(hist.year >= o0) & (hist.year <= o1)]
    book = obligors[obligors.year == cur].copy()

    model = fit(dev)
    default_rates = hist.groupby("year").default_flag.mean()
    ct = float(default_rates.loc[d0:d1].mean())
    cal.calibrate_to_central_tendency(model, dev, ct)

    floor = float(cfg.get("pd_floor", 0.0))
    dev_pd = np.maximum(model.pd(dev), floor)
    oot_pd = np.maximum(model.pd(oot), floor)
    dev_metrics = discrimination(dev.default_flag.values, dev_pd)
    oot_metrics = discrimination(oot.default_flag.values, oot_pd)

    macro_w = macro.pivot(index="period", columns="series_id", values="value")
    sat = fit_satellite(default_rates, macro_w, (d0, d1))

    # Calibration test on out-of-time data. The TTC PD is converted to point-in-time
    # using each year's actual macro conditions, since outcomes are point-in-time.
    u = oot.year.map(macro_w.UNRATE).values
    g = oot.year.map(macro_w.GDP_GROWTH).values
    oot_pit = inv_logit(logit(oot_pd) + sat.b_unemp * (u - sat.u_avg) + sat.b_gdp * (g - sat.g_avg))
    oot_df = oot.assign(pd_ttc=oot_pd, pd_pit=oot_pit, grade=cal.assign_grades(oot_pd, ms))
    order = {gr: i for i, gr in enumerate(grades)}
    binom = cal.binomial_tests(oot_df, "grade", "pd_pit", "default_flag")
    binom_ttc = cal.binomial_tests(oot_df, "grade", "pd_ttc", "default_flag")
    binom = binom.merge(binom_ttc[["grade", "predicted_pd", "status"]].rename(
        columns={"predicted_pd": "ttc_pd", "status": "ttc_status"}), on="grade")
    binom = binom.sort_values("grade", key=lambda s: s.map(order)).reset_index(drop=True)

    book_pd = np.maximum(model.pd(book), floor)
    book["score"] = model.score(book)
    book["pd_ttc"] = book_pd
    book["grade"] = cal.assign_grades(book_pd, ms)
    dev_grades = pd.Series(cal.assign_grades(dev_pd, ms))
    psi_val = cal.psi(dev_grades, book["grade"], grades)

    Xdev = make_features(dev)
    iv = {f: information_value(Xdev[f], dev.default_flag) for f in FEATURES}

    lgd_map: dict[str, float] = cfg["lgd"]
    book["lgd"] = book.seniority.map(lgd_map).fillna(lgd_map.get("SENIOR_UNSECURED", 0.45)).astype(float)
    book["ead"] = book.ead.astype(float)
    rows = []
    for sc in macro_scenarios:
        p = scenario_pd(book_pd, sat, float(sc["unemployment"]), float(sc["gdp_growth"]))
        rows.append(pd.DataFrame({
            "obligor_id": book.obligor_id.values, "scenario_id": sc["id"], "score": book.score.values,
            "grade": book.grade.values, "pd_ttc": book_pd, "pd_scenario": p, "lgd": book.lgd.values,
            "ead": book.ead.values, "expected_loss": p * book.lgd.values * book.ead.values,
        }))
    obligor_pd = pd.concat(rows, ignore_index=True)
    gs = (obligor_pd.groupby(["scenario_id", "grade"])
          .agg(obligors=("obligor_id", "count"), ead=("ead", "sum"), pd_ttc=("pd_ttc", "mean"),
               pd_scenario=("pd_scenario", "mean"), expected_loss=("expected_loss", "sum"))
          .reset_index())
    gs["order"] = gs.grade.map({g: i for i, g in enumerate(grades)})
    gs = gs.sort_values(["scenario_id", "order"]).drop(columns="order").reset_index(drop=True)

    return CreditOutput(model, sat, ct, dev_metrics, oot_metrics, binom, psi_val, iv, model.coefficients(),
                        obligor_pd, gs, default_rates)
