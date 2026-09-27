# Data model

All 25 tables are defined once, in `src/quantrisk/data/schema.py`. `sql/migrations/001_init.sql` is generated from that file (`make ddl`), and CI fails if the two drift apart.

## Inputs (stamped with `load_id`)

| Table | Key | Purpose |
|---|---|---|
| `ref_instrument` | instrument_id | Bond terms: coupon, dates, frequency, day count, spread index, basis, vega. Upserted, never deleted, because other tables reference it |
| `mkt_curve_point` | curve_id, as_of_date, tenor_years | UST par yields. CHECK -5% to 30% |
| `mkt_spread` | spread_index, as_of_date | IG / HY / MBS spread indices (bp) |
| `mkt_vol` | vol_index, as_of_date | Normal implied vol (bp) |
| `mkt_price` | instrument_id, as_of_date, source | Vendor clean price and last-update date (for stale checks) |
| `pos_position` | book_id, instrument_id, as_of_date | Face held (book of record) |
| `ledger_balance` | book_id, instrument_id, as_of_date | Accounting face and market value (for reconciliation) |
| `macro_series` | series_id, period | UNRATE, GDP_GROWTH |
| `credit_obligor` | obligor_id, year | Financial ratios, observed default flag, EAD and seniority for the current book |

## Outputs (keyed by `run_id`)

| Table | Content |
|---|---|
| `risk_run` | Status (RUNNING / SUCCEEDED / FAILED / HELD), times, git SHA, config hash, code version |
| `position_valuation` | Price, accrued, MV, yield, durations, convexity, DV01, vendor price |
| `krd_result` | KRD and DV01 per key tenor |
| `risk_result` | VaR / ES by scope, method, confidence and horizon |
| `pnl_vector` | Hypothetical P&L of today's portfolio on each historical day |
| `backtest_result` | Daily P&L vs VaR 99% with exception flag |
| `stress_scenario`, `stress_result` | Scenario definitions (shock vectors) and P&L by position and leg |
| `credit_pd`, `credit_grade_summary`, `credit_calibration` | Obligor PDs by scenario, grade aggregates, binomial tests |
| `run_summary` | Key/value facts (backtest stats, model coefficients, timings) |

## Controls

| Table | Content |
|---|---|
| `dq_check_result` | Every rule, passing or failing, with row counts and detail |
| `recon_log` | Every reconciliation comparison, with tolerance, status, owner, comment and resolution time |
| `limit_check` | Value, thresholds, utilization and status for each limit |
| `model_validation` | Automated validation test results |

## Lineage query

```sql
-- where did the 99% VaR on the report come from?
SELECT r.run_id, r.git_sha, r.config_hash, s.value_text AS load_id, x.value AS var_99
FROM risk_result x
JOIN risk_run r USING (run_id)
JOIN run_summary s ON s.run_id = r.run_id AND s.key = 'load_id'
WHERE x.scope = 'TOTAL' AND x.metric = 'VaR' AND x.method = 'historical'
  AND x.confidence = 0.99 AND x.horizon_days = 1
ORDER BY r.started_at DESC LIMIT 1;
```
