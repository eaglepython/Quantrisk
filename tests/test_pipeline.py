"""End-to-end: generate sample data, run the batch, check persisted results and controls."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantrisk.data import db, schema
from quantrisk.ingest.dq_rules import DataQualityError
from quantrisk.pipeline import run_daily

from .conftest import AS_OF


def q(engine, table, run_id):
    return db.read_df(engine, schema.metadata.tables[table], run_id=run_id)


def test_run_succeeds_with_lineage(pipeline_run):
    settings, engine, run_id = pipeline_run
    run = db.read_df(engine, schema.risk_run, run_id=run_id).iloc[0]
    assert run.status == "SUCCEEDED"
    assert run.config_hash == settings.hash
    report = settings.path("reports") / str(AS_OF) / f"risk_report_{run_id}.html"
    assert report.exists() and report.stat().st_size > 50_000


def test_cli_accepts_real_data_flag(monkeypatch, tmp_path):
    import quantrisk.cli as cli

    calls = {}

    def fake_generate(as_of, out_dir, **kwargs):
        calls["as_of"] = as_of
        calls["out_dir"] = out_dir
        calls["kwargs"] = kwargs
        return out_dir / as_of.isoformat()

    monkeypatch.setattr("quantrisk.ingest.sources.generate_live_landing", fake_generate)
    monkeypatch.setattr(cli, "load_settings", lambda *args, **kwargs: type("S", (), {
        "path": lambda self, key: tmp_path / key,
        "database_url": "sqlite:///:memory:",
        "hash": "abc123",
    })())
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr("quantrisk.pipeline.run_daily", lambda *args, **kwargs: "R-REAL")

    rc = cli.main(["run", "--as-of", "2026-09-25", "--with-real-data"])

    assert rc == 0
    assert calls["as_of"] == AS_OF
    assert calls["out_dir"] == tmp_path / "raw"


def test_live_ingest_avoids_duplicate_macro_and_fills_curve(monkeypatch, tmp_path):
    import pandas as pd

    from quantrisk.ingest import sources

    def fake_fetch_fred(series_id, start, end, retries=3):
        if series_id == "UNRATE":
            return pd.Series([3.8, 3.7, 3.5], index=pd.to_datetime(["2023-01-31", "2024-01-31", "2025-01-31"]))
        if series_id == "GDPC1":
            return pd.Series(
                [1.0, 1.02, 1.03, 1.05, 1.01, 1.04, 1.06, 1.08],
                index=pd.to_datetime([
                    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
                    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
                ]),
            )
        values = {
            "DGS3MO": [4.5, 4.6],
            "DGS6MO": [4.7, 4.8],
            "DGS1": [4.9, 5.0],
            "DGS2": [5.1, 5.2],
            "DGS3": [5.3, 5.4],
            "DGS5": [5.5, 5.6],
            "DGS7": [5.7, 5.8],
            "DGS10": [5.9, 6.0],
            "DGS20": [6.1, 6.2],
            "DGS30": [6.3, 6.4],
        }
        idx = pd.to_datetime(["2026-09-23", "2026-09-24"])
        return pd.Series(values[series_id], index=idx)

    monkeypatch.setattr(sources, "fetch_fred", fake_fetch_fred)
    out_dir = tmp_path / "live"
    sources.write_treasury_curves(out_dir, date(2023, 1, 2), date(2026, 9, 25))
    sources.write_macro_series(out_dir, 2023, 2025)

    curves = pd.read_csv(out_dir / "curves.csv")
    assert curves["as_of_date"].nunique() == 2
    assert len(curves) == 20
    assert not curves.duplicated(subset=["as_of_date", "tenor_years"]).any()

    macro = pd.read_csv(out_dir / "macro.csv")
    assert macro.groupby(["series_id", "period"]).size().max() == 1


def test_every_position_valued(pipeline_run):
    _, engine, run_id = pipeline_run
    val = q(engine, "position_valuation", run_id)
    assert len(val) == 14
    assert (val.market_value > 0).all()
    assert val.market_value.sum() == pytest.approx(412.7e6, rel=0.05)


def test_var_methods_consistent(pipeline_run):
    _, engine, run_id = pipeline_run
    r = q(engine, "risk_result", run_id)
    t = r[(r.scope == "TOTAL") & (r.horizon_days == 1) & np.isclose(r.confidence, 0.99) & (r.metric == "VaR")]
    vals = t.set_index("method").value
    assert vals.max() / vals.min() < 1.5
    ten = r[(r.scope == "TOTAL") & (r.horizon_days == 10) & np.isclose(r.confidence, 0.99) & (r.metric == "VaR")]
    assert ten.set_index("method").value["historical"] == pytest.approx(vals["historical"] * np.sqrt(10))


def test_stress_directions(pipeline_run):
    _, engine, run_id = pipeline_run
    s = q(engine, "stress_result", run_id).groupby("scenario_id").total_pnl.sum()
    assert s["PAR_UP_100"] < 0 < s["PAR_DN_100"]
    assert s["PAR_UP_200"] < s["PAR_UP_100"]
    assert s["SPREAD_WIDE"] < 0 and s["VOL_UP"] < 0
    # negative convexity of MBS and positive convexity of Treasuries: +100 loss smaller than -100 gain
    assert abs(s["PAR_UP_100"]) < abs(s["PAR_DN_100"])


def test_controls_found_planted_issues(pipeline_run):
    _, engine, run_id = pipeline_run
    dq = db.read_df(engine, schema.dq_check_result, run_id=run_id).set_index("check_name")
    assert dq.loc["price_not_stale", "status"] == "FAIL"
    assert dq.loc["model_vs_vendor_price", "status"] == "FAIL"
    assert (dq[dq.severity == "CRITICAL"].status == "PASS").all()
    recon = db.read_df(engine, schema.recon_log, run_id=run_id)
    brk = recon[recon.status == "BREAK"]
    assert set(brk.measure) == {"face_amount", "market_value"}
    assert brk[brk.measure == "face_amount"].instrument_id.tolist() == ["UST-10Y"]


def test_credit_stress_ordering(pipeline_run):
    _, engine, run_id = pipeline_run
    el = q(engine, "credit_grade_summary", run_id).groupby("scenario_id").expected_loss.sum()
    assert el["BASELINE"] < el["ADVERSE"] < el["SEVERE"]


def test_validation_and_limits_written(pipeline_run):
    _, engine, run_id = pipeline_run
    mv = q(engine, "model_validation", run_id)
    assert {"FI_PRICING", "MR_VAR", "STRESS", "PD_MODEL", "DATA"} <= set(mv.model_id)
    assert (mv[mv.model_id == "FI_PRICING"].status == "PASS").all()
    lim = q(engine, "limit_check", run_id)
    assert len(lim) == 8 and set(lim.status) <= {"GREEN", "AMBER", "RED"}


def test_rerun_is_idempotent_for_inputs(pipeline_run):
    settings, engine, _ = pipeline_run
    before = db.read_sql(engine, "select count(*) as n from mkt_curve_point").n.iloc[0]
    run_daily(AS_OF, settings, make_report=False)
    after = db.read_sql(engine, "select count(*) as n from mkt_curve_point").n.iloc[0]
    assert before == after


def test_critical_dq_failure_holds_run(pipeline_run, tmp_path):
    settings, engine, _ = pipeline_run
    import shutil
    src = settings.path("raw") / str(AS_OF)
    dst = tmp_path / str(AS_OF)
    shutil.copytree(src, dst)
    c = pd.read_csv(dst / "curves.csv")
    c = c[~((c.as_of_date == str(AS_OF)) & (c.tenor_years == 10))]      # drop today's 10Y point
    c.to_csv(dst / "curves.csv", index=False)
    with pytest.raises(DataQualityError):
        run_daily(AS_OF, settings, raw_root=tmp_path, make_report=False)
    held = db.read_sql(engine, "select status from risk_run where status = 'HELD'")
    assert len(held) >= 1
