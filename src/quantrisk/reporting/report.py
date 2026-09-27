"""Automated daily risk report.

Reads only persisted results for a run_id (never recalculates), so the report,
the dashboard and an auditor querying the database all see the same numbers.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import Engine

from quantrisk.config import Settings
from quantrisk.data import db, schema
from quantrisk.governance.validation import load_inventory
from quantrisk.reporting import charts

TEMPLATES = Path(__file__).parent / "templates"


def _summary(engine: Engine, run_id: str) -> dict[str, Any]:
    df = db.read_df(engine, schema.run_summary, run_id=run_id)
    out: dict[str, Any] = {}
    for r in df.itertuples():
        out[r.key] = r.value_text if isinstance(r.value_text, str) else r.value_num
    return out


def usd(x: float | None, digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.{digits}f}"


def usd_m(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x) / 1e6:,.{digits}f}M"


def gather(engine: Engine, run_id: str, settings: Settings) -> dict[str, Any]:
    run = db.read_df(engine, schema.risk_run, run_id=run_id).iloc[0].to_dict()
    as_of = run["as_of_date"]
    summ = _summary(engine, run_id)
    val = db.read_df(engine, schema.position_valuation, run_id=run_id)
    instr = db.read_df(engine, schema.ref_instrument)
    val = val.merge(instr[["instrument_id", "description", "asset_class", "rating", "maturity_date"]], on="instrument_id")
    krd = db.read_df(engine, schema.krd_result, run_id=run_id)
    risk = db.read_df(engine, schema.risk_result, run_id=run_id)
    pnl = db.read_df(engine, schema.pnl_vector, run_id=run_id).sort_values("scenario_date")
    bt = db.read_df(engine, schema.backtest_result, run_id=run_id).sort_values("test_date")
    stress = db.read_df(engine, schema.stress_result, run_id=run_id)
    scen = db.read_df(engine, schema.stress_scenario, run_id=run_id)
    gs = db.read_df(engine, schema.credit_grade_summary, run_id=run_id)
    calib = db.read_df(engine, schema.credit_calibration, run_id=run_id)
    limits = db.read_df(engine, schema.limit_check, run_id=run_id)
    mv = db.read_df(engine, schema.model_validation, run_id=run_id)
    dq = db.read_df(engine, schema.dq_check_result, run_id=run_id)
    recon = db.read_df(engine, schema.recon_log, run_id=run_id)
    recon = recon.astype(object).where(recon.notna(), None)
    dq = dq.astype(object).where(dq.notna(), None)
    mv = mv.astype(object).where(mv.notna(), None)
    macro = db.read_df(engine, schema.macro_series)
    spreads = db.read_df(engine, schema.mkt_spread)
    curves = db.read_df(engine, schema.mkt_curve_point)
    look = int(settings.risk["lookback_days"])
    grades = list(settings.credit["master_scale"])

    # market moves
    dates = sorted(d for d in curves.as_of_date.unique() if d <= as_of)
    d1, d5 = (dates[-2] if len(dates) > 1 else as_of), (dates[-6] if len(dates) > 5 else dates[0])
    cp = curves.pivot(index="as_of_date", columns="tenor_years", values="par_rate")
    sp = spreads.pivot(index="as_of_date", columns="spread_index", values="spread_bp")
    market_rows = []
    for t in (2.0, 5.0, 10.0, 30.0):
        market_rows.append({"name": f"UST {t:g}Y par yield", "level": f"{cp.loc[as_of, t] * 100:.3f}%",
                            "d1": (cp.loc[as_of, t] - cp.loc[d1, t]) * 1e4, "d5": (cp.loc[as_of, t] - cp.loc[d5, t]) * 1e4})
    market_rows.append({"name": "2s10s slope", "level": f"{(cp.loc[as_of, 10.0] - cp.loc[as_of, 2.0]) * 1e4:.0f}bp",
                        "d1": ((cp.loc[as_of, 10.0] - cp.loc[as_of, 2.0]) - (cp.loc[d1, 10.0] - cp.loc[d1, 2.0])) * 1e4,
                        "d5": ((cp.loc[as_of, 10.0] - cp.loc[as_of, 2.0]) - (cp.loc[d5, 10.0] - cp.loc[d5, 2.0])) * 1e4})
    for s in ("IG", "HY", "MBS"):
        market_rows.append({"name": f"{s} spread", "level": f"{sp.loc[as_of, s]:.0f}bp",
                            "d1": sp.loc[as_of, s] - sp.loc[d1, s], "d5": sp.loc[as_of, s] - sp.loc[d5, s]})

    par = json.loads(summ["curve_par"])
    zero = json.loads(summ["curve_zero"])
    prev_par = {f"{t:g}": float(cp.loc[d5, t]) for t in cp.columns}

    def rv(metric: str, method: str, conf: float, h: int = 1, scope: str = "TOTAL") -> float:
        r = risk[(risk.scope == scope) & (risk.metric == metric) & (risk.method == method)
                 & np.isclose(risk.confidence, conf) & (risk.horizon_days == h)]
        return float(r.value.iloc[0]) if len(r) else float("nan")

    var_table = []
    for conf in settings.risk["confidence_levels"]:
        for h in settings.risk["horizons_days"]:
            row: dict[str, Any] = {"label": f"{float(conf) * 100:g}% / {h}d"}
            for m in ("historical", "parametric", "montecarlo"):
                row[f"{m}_var"] = rv("VaR", m, float(conf), int(h))
                row[f"{m}_es"] = rv("ES", m, float(conf), int(h))
            var_table.append(row)
    book_var = []
    for scope in sorted(risk.scope.unique()):
        book_var.append({"scope": scope, "var": rv("VaR", "historical", 0.99, 1, scope),
                         "es": rv("ES", "historical", 0.975, 1, scope),
                         "pvar": rv("VaR", "parametric", 0.99, 1, scope)})

    names = scen.set_index("scenario_id").name.to_dict()
    desc = scen.set_index("scenario_id").description.to_dict()
    by_scen = stress.groupby("scenario_id")[["rates_pnl", "spread_pnl", "vol_pnl", "total_pnl"]].sum()
    by_scen["name"] = by_scen.index.map(names)
    by_scen["description"] = by_scen.index.map(desc)
    by_scen = by_scen.rename(columns={"total_pnl": "total"}).sort_values("total")
    worst_pos = (stress[stress.scenario_id == by_scen.index[0]].sort_values("total_pnl").head(3)
                 .merge(instr[["instrument_id", "description"]], on="instrument_id"))

    hist_pnl = pnl.pnl.values[-look:]
    var99, es99 = rv("VaR", "historical", 0.99), rv("ES", "historical", 0.99)

    macro_w = macro.pivot(index="period", columns="series_id", values="value")
    dr = {int(k): v for k, v in json.loads(summ["default_rates"]).items()}
    el = gs.groupby("scenario_id").expected_loss.sum().to_dict()
    ead = summ.get("credit_book_ead", 0.0)
    gs_view = gs.pivot_table(index="grade", columns="scenario_id", values=["pd_scenario", "expected_loss"]).reindex(grades).dropna(how="all")
    base = gs[gs.scenario_id == "BASELINE"].set_index("grade").reindex(grades).dropna(how="all")
    credit_rows = []
    for g in gs_view.index:
        credit_rows.append({"grade": g, "obligors": int(base.loc[g, "obligors"]), "ead": base.loc[g, "ead"],
                            "pd_ttc": base.loc[g, "pd_ttc"],
                            **{f"pd_{s}": gs_view.loc[g, ("pd_scenario", s)] for s in ("BASELINE", "ADVERSE", "SEVERE")},
                            **{f"el_{s}": gs_view.loc[g, ("expected_loss", s)] for s in ("BASELINE", "ADVERSE", "SEVERE")}})

    lim = limits.copy()
    krd_tenor = krd.groupby("tenor_years").dv01.sum()
    contrib = json.loads(summ["var_contrib"])

    commentary = build_commentary(summ, lim, by_scen, worst_pos, krd_tenor, dq, recon, mv, el, contrib, market_rows)

    return {
        "run": run, "as_of": as_of, "summ": summ, "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "kpis": [
            ("Market value", usd_m(summ["total_market_value"])),
            ("DV01", usd(summ["total_dv01"]) + " / bp"),
            ("Eff. duration", f"{summ['portfolio_eff_duration']:.2f} yrs"),
            ("1d 99% VaR (hist)", usd_m(var99, 2)),
            ("1d 97.5% ES (hist)", usd_m(rv("ES", "historical", 0.975), 2)),
            ("Worst stress", usd_m(by_scen.total.min())),
            ("Credit EL, severe", usd_m(el.get("SEVERE", 0.0))),
        ],
        "limits": lim.to_dict("records"), "commentary": commentary, "market_rows": market_rows,
        "positions": val.sort_values(["book_id", "asset_class", "maturity_date"], ascending=[False, False, True]).to_dict("records"),
        "var_table": var_table, "book_var": book_var, "contrib": contrib,
        "stress": by_scen.reset_index().to_dict("records"), "credit_rows": credit_rows, "el": el, "ead": ead,
        "calib": calib.sort_values("grade", key=lambda c: c.map({g: i for i, g in enumerate(grades)})).to_dict("records"), "validation": mv.to_dict("records"),
        "dq": sorted(dq.to_dict("records"), key=lambda r: (r["status"] != "FAIL", r["severity"] != "CRITICAL", r["check_name"])),
        "recon": sorted(recon.to_dict("records"), key=lambda r: (r["status"] != "BREAK", r["measure"] != "market_value", r["book_id"], r["instrument_id"] or "")), "inventory": load_inventory(settings.config_dir),
        "coef": json.loads(summ["pd_coefficients"]), "iv": json.loads(summ["pd_iv"]),
        "charts": {
            "curve": charts.curve_chart(par, zero, prev_par),
            "krd": charts.krd_chart(krd),
            "hist": charts.pnl_hist(hist_pnl, var99, es99),
            "backtest": charts.backtest_chart(bt),
            "stress": charts.stress_chart(by_scen.reset_index()),
            "credit": charts.credit_chart(gs, grades),
            "dr": charts.default_rate_chart({str(k): v for k, v in dr.items()}, macro_w.UNRATE.to_dict()),
        },
        "usd": usd, "usd_m": usd_m,
    }


def build_commentary(summ: dict[str, Any], lim: pd.DataFrame, by_scen: pd.DataFrame, worst_pos: pd.DataFrame,
                     krd_tenor: pd.Series, dq: pd.DataFrame, recon: pd.DataFrame, mv: pd.DataFrame,
                     el: dict[str, float], contrib: dict[str, float], market_rows: list[dict[str, Any]]) -> list[str]:
    out = []
    flagged = lim[lim.status != "GREEN"]
    if len(flagged):
        items = "; ".join(f"{r.limit_id} at {r.utilization:.0%} of its red limit ({r.status})" for r in flagged.itertuples())
        out.append(f"{len(flagged)} of {len(lim)} limits need attention: {items}.")
    else:
        out.append(f"All {len(lim)} limits are green.")
    tot = sum(contrib.values())
    top = max(contrib, key=lambda k: contrib[k])
    out.append(f"Rates drive the risk: {top.lower()} account for {contrib[top] / tot:.0%} of parametric 99% VaR. "
               f"The largest key-rate exposure is {krd_tenor.idxmax():g}Y at {usd(krd_tenor.max())} per bp.")
    w = by_scen.iloc[0]
    names = ", ".join(worst_pos.description.tolist())
    out.append(f"Worst stress is {w['name']} at {usd_m(w.total)}; the biggest contributors are {names}.")
    ten = next(r for r in market_rows if r["name"].startswith("UST 10Y"))
    out.append(f"10Y Treasury moved {ten['d1']:+.1f}bp on the day and {ten['d5']:+.1f}bp over the week.")
    out.append(f"VaR backtest: {int(summ['bt_exceptions'])} exceptions in {int(summ['bt_n'])} days versus "
               f"{summ['bt_expected']:.1f} expected ({summ['bt_zone']} zone, Kupiec p = {summ['bt_kupiec_p']:.2f}).")
    base, sev = el.get("BASELINE", 0.0), el.get("SEVERE", 0.0)
    out.append(f"Credit expected loss is {usd_m(base)} in the baseline and {usd_m(sev)} in the severely adverse "
               f"scenario ({sev / base:.1f}x). PD model out-of-time AUC {summ['pd_auc_oot']:.3f}, PSI {summ['pd_psi']:.3f}.")
    dqf = dq[(dq.status == "FAIL")]
    brk = recon[recon.status == "BREAK"]
    if len(dqf) or len(brk):
        parts = [f"{r.check_name} ({r.detail})" for r in dqf.itertuples()]
        parts += [f"reconciliation break on {r.book_id} {r.instrument_id or 'book total'} ({usd(r.difference)})" for r in brk.itertuples()]
        out.append("Data issues to clear before sign-off: " + "; ".join(parts) + ".")
    watch = mv[mv.status != "PASS"]
    if len(watch):
        out.append("Model monitoring items: " + ", ".join(f"{r.model_id}.{r.test_name} ({r.status})" for r in watch.itertuples()) + ".")
    return out


def build_report(engine: Engine, run_id: str, settings: Settings) -> Path:
    ctx = gather(engine, run_id, settings)
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("report.html.j2").render(**ctx)
    out_dir = settings.path("reports") / str(ctx["as_of"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"risk_report_{run_id}.html"
    path.write_text(html, encoding="utf-8")
    (settings.path("reports") / "latest.html").write_text(html, encoding="utf-8")
    return path
