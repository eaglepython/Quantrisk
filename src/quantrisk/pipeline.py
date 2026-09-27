"""Daily risk run: one entry point, one run_id.

  1. register run (git SHA, config hash)       6. VaR / ES: historical, parametric, Monte Carlo
  2. ingest + data-quality rules               7. backtesting
  3. read inputs back from the database        8. stress scenarios
  4. curve build, valuation, KRD               9. credit PD, calibration, macro stress
  5. reconciliation to ledger                 10. validation tests, limits, report

Every output row is written with the run_id, so any number on the dashboard
traces back to its data (load_id), code (git SHA) and settings (config hash).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, update

from quantrisk import __version__
from quantrisk.config import Settings, load_settings
from quantrisk.credit.engine import run_credit
from quantrisk.data import db, schema
from quantrisk.data.reconciliation import reconcile
from quantrisk.fixed_income.bonds import Bond, price_scenarios
from quantrisk.fixed_income.curves import bootstrap, fit_nelson_siegel
from quantrisk.fixed_income.portfolio import value_positions
from quantrisk.governance.limits import check_limits
from quantrisk.governance.validation import Check, band
from quantrisk.ingest import loader
from quantrisk.ingest.dq_rules import DataQualityError, model_vs_vendor
from quantrisk.logging_setup import get_logger, run_id_var
from quantrisk.risk import backtest as bt
from quantrisk.risk.factors import SPREADS, Revaluer, build_factor_history
from quantrisk.risk.var import ewma_covariance, parametric_var_es, simulate_t_scenarios, var_es_from_pnl
from quantrisk.stress.engine import build_scenarios, run_stress, scenario_hash

log = get_logger(__name__)


def git_sha() -> str | None:
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"][:40]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
                                       cwd=Path(__file__).parent).decode().strip()[:40]
    except Exception:
        return None


class Timer:
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self._t = time.perf_counter()

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.marks[name] = round(now - self._t, 3)
        self._t = now
        log.info(f"step complete: {name}", extra={"ctx": {"step": name, "seconds": self.marks[name]}})


def _set_status(engine: Engine, run_id: str, status: str, message: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(update(schema.risk_run).where(schema.risk_run.c.run_id == run_id)
                     .values(status=status, finished_at=datetime.utcnow(), message=message))


def run_daily(as_of: date, settings: Settings | None = None, raw_root: Path | None = None,
              make_report: bool = True) -> str:
    settings = settings or load_settings()
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    run_id = f"R-{as_of:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    run_id_var.set(run_id)
    with engine.begin() as conn:
        conn.execute(schema.risk_run.insert().values(
            run_id=run_id, as_of_date=as_of, status="RUNNING", started_at=datetime.utcnow(),
            git_sha=git_sha(), config_hash=settings.hash, code_version=__version__))
    log.info("run started", extra={"ctx": {"as_of": str(as_of), "config_hash": settings.hash}})
    timer = Timer()
    try:
        _run(engine, settings, as_of, run_id, raw_root, timer)
    except DataQualityError as e:
        _write_dq(engine, run_id, e.failures)
        _set_status(engine, run_id, "HELD", f"critical data-quality failure: {e}")
        log.error("run held on data quality", extra={"ctx": {"reason": str(e)}})
        raise
    except Exception as e:
        _set_status(engine, run_id, "FAILED", repr(e)[:2000])
        log.exception("run failed")
        raise
    db.replace_rows(engine, schema.run_summary, pd.DataFrame([
        {"run_id": run_id, "key": f"seconds_{k}", "value_num": v, "value_text": None} for k, v in timer.marks.items()
    ]), where={"run_id": run_id}, where_in={"key": [f"seconds_{k}" for k in timer.marks]})
    _set_status(engine, run_id, "SUCCEEDED")
    if make_report:
        from quantrisk.reporting.report import build_report
        try:
            path = build_report(engine, run_id, settings)
            log.info("report written", extra={"ctx": {"path": str(path)}})
        except Exception as e:  # results are stored; a report failure must not fail the run
            _set_status(engine, run_id, "SUCCEEDED", f"report generation failed: {e!r}"[:2000])
            log.exception("report generation failed")
    log.info("run succeeded", extra={"ctx": {"total_seconds": round(sum(timer.marks.values()), 2)}})
    return run_id


def _write_dq(engine: Engine, run_id: str, results: list[Any]) -> None:
    df = pd.DataFrame([r.as_row() for r in results])
    df = df.drop_duplicates("check_name", keep="last")
    df["run_id"] = run_id
    db.replace_rows(engine, schema.dq_check_result, df, where={"run_id": run_id})


def _run(engine: Engine, s: Settings, as_of: date, run_id: str, raw_root: Path | None, timer: Timer) -> None:
    summary: dict[str, Any] = {}

    # ---------------------------------------------------------------- 2. ingest
    load_id, dq = loader.load(engine, s, as_of, raw_root)
    summary["load_id"] = load_id
    timer.lap("ingest")

    # ---------------------------------------------------------------- 3. read back
    instruments = db.read_df(engine, schema.ref_instrument)
    positions = db.read_df(engine, schema.pos_position, as_of_date=as_of)
    curves = db.read_df(engine, schema.mkt_curve_point, curve_id=s.market["curve_id"])
    curves = curves[curves.as_of_date <= as_of]
    spreads = db.read_df(engine, schema.mkt_spread)
    spreads = spreads[spreads.as_of_date <= as_of]
    vols = db.read_df(engine, schema.mkt_vol)
    vols = vols[vols.as_of_date <= as_of]
    prices = db.read_df(engine, schema.mkt_price, as_of_date=as_of)
    ledger = db.read_df(engine, schema.ledger_balance, as_of_date=as_of)
    macro = db.read_df(engine, schema.macro_series)
    obligors = db.read_df(engine, schema.credit_obligor)
    timer.lap("read_inputs")

    # ---------------------------------------------------------------- 4. fixed income
    today = curves[curves.as_of_date == as_of].sort_values("tenor_years")
    tenors = today.tenor_years.astype(float).tolist()
    zc = bootstrap(tenors, today.par_rate.tolist())
    sp_today = spreads[spreads.as_of_date == as_of].set_index("spread_index").spread_bp.astype(float).to_dict()
    key_tenors = [float(k) for k in s.market["key_tenors"]]
    valuation, krd = value_positions(positions, instruments, zc, sp_today, as_of, key_tenors, s.risk["bump_bp"])
    valuation = valuation.merge(prices[["instrument_id", "clean_price"]].rename(columns={"clean_price": "vendor_price"}),
                                on="instrument_id", how="left")
    dq.append(model_vs_vendor(valuation, s.dq["model_vs_vendor_price_tol"]))
    _write_dq(engine, run_id, dq)
    db.replace_rows(engine, schema.position_valuation, valuation.assign(run_id=run_id), where={"run_id": run_id})
    db.replace_rows(engine, schema.krd_result, krd.assign(run_id=run_id), where={"run_id": run_id})
    summary["curve_par"] = json.dumps({f"{t:g}": float(r) for t, r in zip(tenors, today.par_rate, strict=True)})
    summary["curve_zero"] = json.dumps({f"{t:g}": float(zc.zero(t)) for t in tenors})
    ns_params, ns_rmse = fit_nelson_siegel(tenors, today.par_rate.astype(float).tolist())
    timer.lap("valuation")

    # ---------------------------------------------------------------- 5. reconciliation
    recon = reconcile(run_id, as_of, positions, valuation, ledger, s.recon["market_value_tol_pct"], s.recon["quantity_tol"])
    db.replace_rows(engine, schema.recon_log, recon, where={"run_id": run_id})
    breaks = int((recon.status == "BREAK").sum())
    timer.lap("reconciliation")

    # ---------------------------------------------------------------- 6. VaR / ES
    hist, _ = build_factor_history(curves, spreads, vols)
    reval = Revaluer(positions, instruments, zc, sp_today, as_of, tenors)
    look = int(s.risk["lookback_days"])
    pos_pnl_all = reval.position_pnl(hist.changes)                     # N x P full revaluation
    pnl_all = pos_pnl_all.sum(axis=1)
    db.replace_rows(engine, schema.pnl_vector, pd.DataFrame({"run_id": run_id, "scenario_date": hist.dates,
                                                              "pnl": pnl_all}), where={"run_id": run_id})
    books = positions.book_id.tolist()
    scopes = {"TOTAL": np.ones(len(books), dtype=bool)} | {b: np.array([x == b for x in books]) for b in sorted(set(books))}

    sens = reval.sensitivities()                                        # P x F
    cov = ewma_covariance(hist.changes[-look:], float(s.risk["ewma_lambda"]))
    mc_scen = simulate_t_scenarios(cov, int(s.risk["mc_paths"]), int(s.risk["mc_dof"]), int(s.risk["mc_seed"]))
    mc_pos = reval.position_pnl(mc_scen)
    rows = []
    for scope, mask in scopes.items():
        hp = pos_pnl_all[-look:, mask].sum(axis=1)
        mp = mc_pos[:, mask].sum(axis=1)
        sv = sens[mask].sum(axis=0)
        for c in s.risk["confidence_levels"]:
            c = float(c)
            h_var, h_es = var_es_from_pnl(hp, c)
            p_var, p_es, _ = parametric_var_es(sv, cov, c)
            m_var, m_es = var_es_from_pnl(mp, c)
            for method, v, e in (("historical", h_var, h_es), ("parametric", p_var, p_es), ("montecarlo", m_var, m_es)):
                for hday in s.risk["horizons_days"]:
                    f = float(np.sqrt(int(hday)))
                    rows.append({"scope": scope, "metric": "VaR", "method": method, "confidence": c,
                                 "horizon_days": int(hday), "value": v * f})
                    rows.append({"scope": scope, "metric": "ES", "method": method, "confidence": c,
                                 "horizon_days": int(hday), "value": e * f})
    risk_df = pd.DataFrame(rows).assign(run_id=run_id)
    db.replace_rows(engine, schema.risk_result, risk_df, where={"run_id": run_id})
    # risk contribution by factor group (parametric, 99%): Euler allocation
    sv_tot = sens.sum(axis=0)
    sigma = float(np.sqrt(sv_tot @ cov @ sv_tot))
    contrib = sv_tot * (cov @ sv_tot) / sigma
    groups = {"Rates": contrib[:len(tenors)].sum(), "Spreads": contrib[len(tenors):len(tenors) + len(SPREADS)].sum(),
              "Volatility": contrib[-1]}
    summary["var_contrib"] = json.dumps({k: float(v) for k, v in groups.items()})
    summary["factor_names"] = json.dumps(hist.names)
    summary["history_days"] = len(hist.dates)
    timer.lap("var_es")

    # ---------------------------------------------------------------- 7. backtest
    realized, var99 = bt.rolling_historical_var(pnl_all, look, int(s.risk["backtest_window"]), 0.99)
    summ, hits = bt.summarize(realized, var99, 0.99)
    n_bt = len(realized)
    db.replace_rows(engine, schema.backtest_result, pd.DataFrame({
        "run_id": run_id, "test_date": hist.dates[-n_bt:], "pnl": realized, "var_99": var99, "exception": hits}),
        where={"run_id": run_id})
    timer.lap("backtest")

    # ---------------------------------------------------------------- 8. stress
    scen_cfg = s.scenarios["market_scenarios"]
    scens = build_scenarios(scen_cfg, tenors, hist)
    stress = run_stress(reval, scens, hist, len(tenors))
    shash = scenario_hash(scen_cfg)
    db.replace_rows(engine, schema.stress_result, stress.assign(run_id=run_id, scenario_hash=shash), where={"run_id": run_id})
    db.replace_rows(engine, schema.stress_scenario, pd.DataFrame([
        {"run_id": run_id, "scenario_id": sc.id, "name": sc.name, "description": sc.description,
         "shock_vector": json.dumps([round(float(x), 4) for x in sc.vector])} for sc in scens]), where={"run_id": run_id})
    by_scen = stress.groupby("scenario_id").total_pnl.sum()
    timer.lap("stress")

    # ---------------------------------------------------------------- 9. credit
    obligors = obligors.copy()
    obligors["default_flag"] = pd.to_numeric(obligors.default_flag, errors="coerce")
    credit = run_credit(obligors, macro, s.credit, s.scenarios["macro_scenarios"])
    db.replace_rows(engine, schema.credit_pd, credit.obligor_pd.assign(run_id=run_id), where={"run_id": run_id})
    db.replace_rows(engine, schema.credit_grade_summary, credit.grade_summary.assign(run_id=run_id), where={"run_id": run_id})
    cal_df = credit.binomial.drop(columns=["ttc_status"]).assign(run_id=run_id)
    db.replace_rows(engine, schema.credit_calibration, cal_df, where={"run_id": run_id})
    el = credit.grade_summary.groupby("scenario_id").expected_loss.sum()
    timer.lap("credit")

    # ---------------------------------------------------------------- 10. validation + limits
    checks = _validation_checks(s, as_of, zc, instruments, valuation, krd, ns_rmse, summ, risk_df, stress, by_scen,
                                sens, credit, dq, breaks, reval)
    db.replace_rows(engine, schema.model_validation,
                    pd.DataFrame([c.as_row() for c in checks]).assign(run_id=run_id), where={"run_id": run_id})

    tot = risk_df[(risk_df.scope == "TOTAL") & (risk_df.method == "historical") & (risk_df.horizon_days == 1)]
    metrics = {
        "var_hist_99_1d": float(tot[(tot.metric == "VaR") & np.isclose(tot.confidence, 0.99)].value.iloc[0]),
        "es_hist_975_1d": float(tot[(tot.metric == "ES") & np.isclose(tot.confidence, 0.975)].value.iloc[0]),
        "dv01": float(abs(valuation.dv01.sum())),
        "stress_worst_loss": float(max(0.0, -by_scen.min())),
        "backtest_exceptions_99": float(summ.exceptions),
        "pd_psi": credit.psi,
        "el_severe": float(el.get("SEVERE", 0.0)),
        "recon_breaks": float(breaks),
    }
    limits = check_limits(metrics, s.limits).assign(run_id=run_id)
    db.replace_rows(engine, schema.limit_check, limits, where={"run_id": run_id})

    summary.update({
        "ns_rmse_bp": ns_rmse, "ns_params": json.dumps([float(x) for x in ns_params]),
        "bt_exceptions": summ.exceptions, "bt_expected": summ.expected, "bt_zone": summ.zone,
        "bt_kupiec_p": summ.kupiec_p, "bt_christoffersen_p": summ.christoffersen_p, "bt_n": summ.n,
        "pd_central_tendency": credit.central_tendency, "pd_auc_dev": credit.dev_metrics["auc"],
        "pd_auc_oot": credit.oot_metrics["auc"], "pd_gini_dev": credit.dev_metrics["gini"],
        "pd_gini_oot": credit.oot_metrics["gini"], "pd_ks_oot": credit.oot_metrics["ks"], "pd_psi": credit.psi,
        "pd_coefficients": json.dumps(credit.coefficients), "pd_iv": json.dumps(credit.iv),
        "pd_offset": credit.model.offset, "sat_b_unemp": credit.satellite.b_unemp,
        "sat_b_gdp": credit.satellite.b_gdp, "sat_r2": credit.satellite.r2, "sat_u_avg": credit.satellite.u_avg,
        "sat_g_avg": credit.satellite.g_avg,
        "default_rates": json.dumps({int(k): float(v) for k, v in credit.default_rates.items()}),
        "recon_breaks": breaks, "stress_scenario_hash": shash, "lookback_days": look, "total_market_value": float(valuation.market_value.sum()),
        "total_dv01": float(valuation.dv01.sum()),
        "portfolio_eff_duration": float((valuation.eff_duration * valuation.market_value).sum() / valuation.market_value.sum()),
        "credit_book_ead": float(credit.obligor_pd[credit.obligor_pd.scenario_id == "BASELINE"].ead.sum()),
        "credit_book_obligors": int((credit.obligor_pd.scenario_id == "BASELINE").sum()),
    })
    srows: list[dict[str, Any]] = []
    for k, v in summary.items():
        if isinstance(v, str):
            srows.append({"run_id": run_id, "key": k, "value_num": None, "value_text": v})
        else:
            srows.append({"run_id": run_id, "key": k, "value_num": float(v), "value_text": None})
    db.replace_rows(engine, schema.run_summary, pd.DataFrame(srows), where={"run_id": run_id})
    timer.lap("governance")


def _validation_checks(s: Settings, as_of: date, zc: Any, instruments: pd.DataFrame, valuation: pd.DataFrame,
                       krd: pd.DataFrame, ns_rmse: float, summ: bt.BacktestSummary, risk_df: pd.DataFrame,
                       stress: pd.DataFrame, by_scen: pd.Series, sens: np.ndarray, credit: Any,
                       dq: list[Any], breaks: int, reval: Revaluer) -> list[Check]:
    ch: list[Check] = []
    # FI: a synthetic 10y par bond off today's curve must price at 100
    par10 = zc.par_rate(10.0)
    pb = Bond("PAR10", "GOVT", par10, date(as_of.year + 10, as_of.month, as_of.day), as_of, 2, "ACT/ACT")
    p = float(price_scenarios(pb, as_of, zc)[0])
    ch.append(Check("FI_PRICING", "par_bond_reprices_to_100", p, "|P-100| < 0.01", band(abs(p - 100), lambda v: v < 0.01, lambda v: v < 0.05)))
    k = krd.groupby("instrument_id").krd.sum()
    ed = valuation.drop_duplicates("instrument_id").set_index("instrument_id").eff_duration
    gap = float((k - ed.reindex(k.index)).abs().max())
    ch.append(Check("FI_PRICING", "krd_sum_equals_eff_duration", gap, "max gap < 0.01y", band(gap, lambda v: v < 0.01, lambda v: v < 0.05)))
    gv = valuation[valuation.instrument_id.str.startswith("UST")].drop_duplicates("instrument_id")
    tdiff = float((gv.clean_price - gv.vendor_price).abs().max()) if len(gv) else 0.0
    ch.append(Check("FI_PRICING", "treasury_model_vs_vendor", tdiff, "max < 0.10 pts", band(tdiff, lambda v: v < 0.10, lambda v: v < 0.25)))
    ch.append(Check("FI_PRICING", "nelson_siegel_benchmark_rmse_bp", ns_rmse, "< 5bp (benchmark)", band(ns_rmse, lambda v: v < 5, lambda v: v < 10),
                    "Independent parametric curve fit agrees with bootstrapped inputs"))
    # VaR
    ch.append(Check("MR_VAR", "backtest_traffic_light", float(summ.exceptions), "GREEN 0-4 / AMBER 5-9 / RED 10+",
                    {"GREEN": "PASS", "AMBER": "WATCH", "RED": "FAIL"}[summ.zone], f"{summ.exceptions} exceptions in {summ.n} days"))
    ch.append(Check("MR_VAR", "kupiec_pof_pvalue", summ.kupiec_p, "p > 0.05", band(summ.kupiec_p, lambda v: v > 0.05, lambda v: v > 0.01)))
    ch.append(Check("MR_VAR", "christoffersen_independence_pvalue", summ.christoffersen_p, "p > 0.05",
                    band(summ.christoffersen_p, lambda v: v > 0.05, lambda v: v > 0.01)))
    t = risk_df[(risk_df.scope == "TOTAL") & (risk_df.horizon_days == 1)]
    ok = all(t[(t.metric == "ES") & (t.method == m) & np.isclose(t.confidence, c)].value.iloc[0]
             >= t[(t.metric == "VaR") & (t.method == m) & np.isclose(t.confidence, c)].value.iloc[0] - 1e-6
             for m in ("historical", "parametric", "montecarlo") for c in s.risk["confidence_levels"])
    ch.append(Check("MR_VAR", "es_at_least_var", 1.0 if ok else 0.0, "ES >= VaR for all methods", "PASS" if ok else "FAIL"))
    hv = t[(t.metric == "VaR") & (t.method == "historical") & np.isclose(t.confidence, 0.99)].value.iloc[0]
    mv = t[(t.metric == "VaR") & (t.method == "montecarlo") & np.isclose(t.confidence, 0.99)].value.iloc[0]
    ratio = float(mv / hv)
    ch.append(Check("MR_VAR", "mc_vs_historical_var99_ratio", ratio, "0.75 to 1.33", band(ratio, lambda v: 0.75 <= v <= 1.33, lambda v: 0.6 <= v <= 1.6),
                    "Benchmark: two independent methods should broadly agree"))
    # Stress: sensitivity approximation vs full revaluation for parallel +100
    if "PAR_UP_100" in by_scen.index:
        n_r = reval.n_rates
        shock = np.zeros(sens.shape[1])
        shock[:n_r] = 100.0
        approx = float((sens @ shock).sum())
        full = float(by_scen["PAR_UP_100"])
        err = abs(approx - full) / max(abs(full), 1.0)
        ch.append(Check("STRESS", "delta_vs_full_reval_par100", err, "< 10% (convexity effect)",
                        band(err, lambda v: v < 0.10, lambda v: v < 0.20),
                        f"delta-only {approx:,.0f} vs full revaluation {full:,.0f}"))
    # PD model
    auc = credit.oot_metrics["auc"]
    ch.append(Check("PD_MODEL", "oot_auc", auc, ">= 0.70", band(auc, lambda v: v >= 0.70, lambda v: v >= 0.65)))
    drop = credit.dev_metrics["gini"] - credit.oot_metrics["gini"]
    ch.append(Check("PD_MODEL", "gini_deterioration_dev_to_oot", drop, "< 0.10", band(drop, lambda v: v < 0.10, lambda v: v < 0.15)))
    ch.append(Check("PD_MODEL", "psi_grade_distribution", credit.psi, "< 0.10", band(credit.psi, lambda v: v < 0.10, lambda v: v < 0.25)))
    fails = int((credit.binomial.status == "FAIL").sum())
    ch.append(Check("PD_MODEL", "binomial_calibration_pit_fails", float(fails), "0 grades failing at 1%",
                    "PASS" if fails == 0 else "WATCH" if fails == 1 else "FAIL"))
    ttc_fails = int((credit.binomial.ttc_status == "FAIL").sum())
    ch.append(Check("PD_MODEL", "binomial_calibration_ttc_fails", float(ttc_fails), "informational",
                    "WATCH" if ttc_fails else "PASS",
                    "TTC PDs exceed benign-period defaults by design; PIT test is the binding one"))
    ch.append(Check("PD_MODEL", "macro_satellite_r2", credit.satellite.r2, ">= 0.50", band(credit.satellite.r2, lambda v: v >= 0.5, lambda v: v >= 0.3)))
    # Data
    warn = sum(1 for r in dq if r.severity == "WARNING" and r.status == "FAIL")
    ch.append(Check("DATA", "dq_warnings", float(warn), "0", "PASS" if warn == 0 else "WATCH"))
    ch.append(Check("DATA", "reconciliation_breaks", float(breaks), "0", "PASS" if breaks == 0 else "WATCH" if breaks < 3 else "FAIL"))
    return ch
