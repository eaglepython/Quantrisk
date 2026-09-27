"""Relational data model (single source of truth for PostgreSQL and SQLite).

Four groups of tables:
  ref_*   static reference data (instrument terms)
  mkt_* / pos_* / macro_* / credit_obligor   daily or periodic inputs, each row stamped with load_id
  risk_run + *_result   outputs, every row keyed by run_id for lineage
  dq_check_result / recon_log / limit_check / model_validation   controls
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

# ---------------------------------------------------------------- reference
ref_instrument = Table(
    "ref_instrument", metadata,
    Column("instrument_id", String(32), primary_key=True),
    Column("description", String(120), nullable=False),
    Column("asset_class", String(8), nullable=False),        # GOVT / CORP / MBS
    Column("issuer", String(64), nullable=False),
    Column("sector", String(32), nullable=False),
    Column("rating", String(8), nullable=False),
    Column("spread_index", String(8), nullable=True),        # IG / HY / MBS / NULL for govts
    Column("currency", String(3), nullable=False, default="USD"),
    Column("coupon", Float, nullable=False),
    Column("issue_date", Date, nullable=False),
    Column("maturity_date", Date, nullable=False),
    Column("frequency", Integer, nullable=False),
    Column("day_count", String(12), nullable=False),
    Column("spread_basis_bp", Float, nullable=False, default=0.0),
    Column("vega_per_100", Float, nullable=False, default=0.0),  # price change per +1 normal-vol point
    CheckConstraint("coupon >= 0 AND coupon < 0.25", name="ck_instr_coupon"),
    CheckConstraint("frequency IN (1, 2, 4, 12)", name="ck_instr_freq"),
    CheckConstraint("maturity_date > issue_date", name="ck_instr_dates"),
)

# ---------------------------------------------------------------- market / positions
mkt_curve_point = Table(
    "mkt_curve_point", metadata,
    Column("curve_id", String(16), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("tenor_years", Float, primary_key=True),
    Column("par_rate", Float, nullable=False),
    Column("source", String(32), nullable=False),
    Column("load_id", String(40), nullable=False),
    CheckConstraint("par_rate > -0.05 AND par_rate < 0.30", name="ck_curve_rate"),
    CheckConstraint("tenor_years > 0", name="ck_curve_tenor"),
)
Index("ix_curve_date", mkt_curve_point.c.as_of_date)

mkt_spread = Table(
    "mkt_spread", metadata,
    Column("spread_index", String(8), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("spread_bp", Float, nullable=False),
    Column("source", String(32), nullable=False),
    Column("load_id", String(40), nullable=False),
)

mkt_vol = Table(
    "mkt_vol", metadata,
    Column("vol_index", String(16), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("normal_vol_bp", Float, nullable=False),
    Column("source", String(32), nullable=False),
    Column("load_id", String(40), nullable=False),
)

mkt_price = Table(
    "mkt_price", metadata,
    Column("instrument_id", String(32), ForeignKey("ref_instrument.instrument_id"), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("source", String(32), primary_key=True),
    Column("clean_price", Float, nullable=False),
    Column("price_date", Date, nullable=False),          # date the vendor last updated this price
    Column("load_id", String(40), nullable=False),
    CheckConstraint("clean_price > 0", name="ck_price_pos"),
)

pos_position = Table(
    "pos_position", metadata,
    Column("book_id", String(16), primary_key=True),
    Column("instrument_id", String(32), ForeignKey("ref_instrument.instrument_id"), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("face_amount", Float, nullable=False),
    Column("source_system", String(32), nullable=False),
    Column("load_id", String(40), nullable=False),
)

ledger_balance = Table(
    "ledger_balance", metadata,
    Column("book_id", String(16), primary_key=True),
    Column("instrument_id", String(32), primary_key=True),
    Column("as_of_date", Date, primary_key=True),
    Column("face_amount", Float, nullable=False),
    Column("market_value", Float, nullable=False),
    Column("load_id", String(40), nullable=False),
)

macro_series = Table(
    "macro_series", metadata,
    Column("series_id", String(16), primary_key=True),
    Column("period", Integer, primary_key=True),          # year
    Column("value", Float, nullable=False),
    Column("source", String(32), nullable=False),
    Column("load_id", String(40), nullable=False),
)

credit_obligor = Table(
    "credit_obligor", metadata,
    Column("obligor_id", String(16), primary_key=True),
    Column("year", Integer, primary_key=True),
    Column("sector", String(24), nullable=False),
    Column("leverage", Float, nullable=False),
    Column("interest_coverage", Float, nullable=False),
    Column("roa", Float, nullable=False),
    Column("current_ratio", Float, nullable=False),
    Column("log_assets", Float, nullable=False),
    Column("default_flag", Integer, nullable=True),        # observed next-12m default; NULL if not yet observed
    Column("ead", Float, nullable=True),
    Column("seniority", String(16), nullable=True),
    Column("load_id", String(40), nullable=False),
    CheckConstraint("leverage >= 0 AND leverage <= 1.5", name="ck_obl_lev"),
)

# ---------------------------------------------------------------- runs and results
risk_run = Table(
    "risk_run", metadata,
    Column("run_id", String(40), primary_key=True),
    Column("as_of_date", Date, nullable=False),
    Column("status", String(16), nullable=False),         # RUNNING / SUCCEEDED / FAILED / HELD
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=True),
    Column("git_sha", String(40), nullable=True),
    Column("config_hash", String(16), nullable=False),
    Column("code_version", String(16), nullable=False),
    Column("message", Text, nullable=True),
)

position_valuation = Table(
    "position_valuation", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("book_id", String(16), primary_key=True),
    Column("instrument_id", String(32), primary_key=True),
    Column("face_amount", Float, nullable=False),
    Column("clean_price", Float, nullable=False),
    Column("dirty_price", Float, nullable=False),
    Column("accrued", Float, nullable=False),
    Column("market_value", Float, nullable=False),
    Column("ytm", Float, nullable=False),
    Column("mod_duration", Float, nullable=False),
    Column("eff_duration", Float, nullable=False),
    Column("convexity", Float, nullable=False),
    Column("spread_duration", Float, nullable=False),
    Column("dv01", Float, nullable=False),
    Column("vendor_price", Float, nullable=True),
)

krd_result = Table(
    "krd_result", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("book_id", String(16), primary_key=True),
    Column("instrument_id", String(32), primary_key=True),
    Column("tenor_years", Float, primary_key=True),
    Column("krd", Float, nullable=False),
    Column("dv01", Float, nullable=False),
)

risk_result = Table(
    "risk_result", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("scope", String(16), primary_key=True),        # TOTAL or book_id
    Column("metric", String(16), primary_key=True),       # VaR / ES
    Column("method", String(16), primary_key=True),       # historical / parametric / montecarlo
    Column("confidence", Float, primary_key=True),
    Column("horizon_days", Integer, primary_key=True),
    Column("value", Float, nullable=False),
)

pnl_vector = Table(
    "pnl_vector", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("scenario_date", Date, primary_key=True),
    Column("pnl", Float, nullable=False),
)

backtest_result = Table(
    "backtest_result", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("test_date", Date, primary_key=True),
    Column("pnl", Float, nullable=False),
    Column("var_99", Float, nullable=False),
    Column("exception", Integer, nullable=False),
)

stress_result = Table(
    "stress_result", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("scenario_id", String(24), primary_key=True),
    Column("book_id", String(16), primary_key=True),
    Column("instrument_id", String(32), primary_key=True),
    Column("rates_pnl", Float, nullable=False),
    Column("spread_pnl", Float, nullable=False),
    Column("vol_pnl", Float, nullable=False),
    Column("total_pnl", Float, nullable=False),
    Column("scenario_hash", String(16), nullable=False),
)

credit_pd = Table(
    "credit_pd", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("obligor_id", String(16), primary_key=True),
    Column("scenario_id", String(16), primary_key=True),
    Column("score", Float, nullable=False),
    Column("grade", String(4), nullable=False),
    Column("pd_ttc", Float, nullable=False),
    Column("pd_scenario", Float, nullable=False),
    Column("lgd", Float, nullable=False),
    Column("ead", Float, nullable=False),
    Column("expected_loss", Float, nullable=False),
)

# ---------------------------------------------------------------- controls
dq_check_result = Table(
    "dq_check_result", metadata,
    Column("run_id", String(40), primary_key=True),
    Column("check_name", String(48), primary_key=True),
    Column("table_name", String(32), nullable=False),
    Column("severity", String(8), nullable=False),       # CRITICAL / WARNING
    Column("rows_checked", Integer, nullable=False),
    Column("rows_failed", Integer, nullable=False),
    Column("status", String(8), nullable=False),         # PASS / FAIL
    Column("detail", Text, nullable=True),
)

recon_log = Table(
    "recon_log", metadata,
    Column("recon_id", String(64), primary_key=True),
    Column("run_id", String(40), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("book_id", String(16), nullable=False),
    Column("instrument_id", String(32), nullable=True),
    Column("measure", String(24), nullable=False),
    Column("value_risk", Float, nullable=True),
    Column("value_ledger", Float, nullable=True),
    Column("difference", Float, nullable=True),
    Column("tolerance", Float, nullable=False),
    Column("status", String(8), nullable=False),         # MATCH / BREAK
    Column("owner", String(32), nullable=True),
    Column("comment", Text, nullable=True),
    Column("resolved_at", DateTime, nullable=True),
)

limit_check = Table(
    "limit_check", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("limit_id", String(24), primary_key=True),
    Column("description", String(120), nullable=False),
    Column("value", Float, nullable=False),
    Column("amber", Float, nullable=False),
    Column("red", Float, nullable=False),
    Column("utilization", Float, nullable=False),
    Column("status", String(8), nullable=False),         # GREEN / AMBER / RED
)

model_validation = Table(
    "model_validation", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("model_id", String(24), primary_key=True),
    Column("test_name", String(48), primary_key=True),
    Column("value", Float, nullable=True),
    Column("threshold", String(48), nullable=True),
    Column("status", String(8), nullable=False),         # PASS / WATCH / FAIL
    Column("detail", Text, nullable=True),
)

run_summary = Table(
    "run_summary", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("key", String(64), primary_key=True),
    Column("value_num", Float, nullable=True),
    Column("value_text", Text, nullable=True),
)

stress_scenario = Table(
    "stress_scenario", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("scenario_id", String(24), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("description", Text, nullable=True),
    Column("shock_vector", Text, nullable=False),      # JSON list, factor order in run_summary
)

credit_grade_summary = Table(
    "credit_grade_summary", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("scenario_id", String(16), primary_key=True),
    Column("grade", String(4), primary_key=True),
    Column("obligors", Integer, nullable=False),
    Column("ead", Float, nullable=False),
    Column("pd_ttc", Float, nullable=False),
    Column("pd_scenario", Float, nullable=False),
    Column("expected_loss", Float, nullable=False),
)

credit_calibration = Table(
    "credit_calibration", metadata,
    Column("run_id", String(40), ForeignKey("risk_run.run_id"), primary_key=True),
    Column("grade", String(4), primary_key=True),
    Column("n", Integer, nullable=False),
    Column("defaults", Integer, nullable=False),
    Column("observed_dr", Float, nullable=False),
    Column("predicted_pd", Float, nullable=False),
    Column("ttc_pd", Float, nullable=False),
    Column("p_value", Float, nullable=False),
    Column("status", String(8), nullable=False),
)

RESULT_TABLES = [position_valuation, krd_result, risk_result, pnl_vector, backtest_result,
                 stress_result, stress_scenario, credit_pd, credit_grade_summary, credit_calibration,
                 limit_check, model_validation, run_summary]
