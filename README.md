# QuantRisk Platform

**Fixed-income, market-risk and credit-risk analytics, run end to end every business day.**

I built QuantRisk to do what a bank or treasury risk desk does every evening. It pulls in rates, prices, spreads and positions, checks the data, prices every bond, measures how much the portfolio could lose, stress tests it, estimates credit losses under recession scenarios, and publishes a report someone can trust at 8 a.m. Every number traces back to its input data, code version and configuration.

It runs locally in about 7 seconds on a $413M sample portfolio (14 bonds, 2 books) and a 3,000-obligor loan book, on SQLite or PostgreSQL.

```
quantrisk run --as-of 2026-09-25 --with-sample      # full batch + HTML risk report
streamlit run dashboards/app.py                      # interactive dashboard
docker compose up --build                            # Postgres + batch + dashboard
```

| Automated risk report | Streamlit dashboard |
|---|---|
| ![Risk report](docs/img/report.png) | ![Dashboard](docs/img/dashboard.png) |

---

## What it does

| Layer | What's in the code | Where |
|---|---|---|
| **Market-data ingestion** | Landing files for UST par curves, bond prices, IG/HY/MBS spread indices, vol index, positions, ledger, macro and obligor data. Typed, validated and loaded idempotently with a `load_id`. FRED extractor for real Treasury data. | `ingest/` |
| **Fixed-income engine** | Par-curve bootstrap to zero rates, Nelson-Siegel benchmark fit, bullet bond and agency MBS pricing (S-curve prepayment model), yield, modified and effective duration, convexity, spread duration, DV01, key-rate durations (2Y/5Y/10Y/30Y triangular bumps). | `fixed_income/` |
| **Risk engine** | Historical-simulation, parametric (delta-normal, EWMA) and Monte Carlo (multivariate Student t, full revaluation) VaR and Expected Shortfall at 95/97.5/99%, 1 and 10 days, by book. Euler risk contributions. Backtesting with Basel traffic light, Kupiec and Christoffersen tests. | `risk/` |
| **Stress testing** | Config-driven scenarios: parallel +/-100 and +200bp, bear steepener, bear and bull flattener, IG/HY/MBS spread widening, volatility shock, risk-off combination, and a replay of the worst 10-day window in history. Full revaluation with rates/spread/vol decomposition. | `stress/`, `config/scenarios.yaml` |
| **Credit module** | Logistic PD scorecard on financial ratios, calibration to long-run central tendency, rating master scale, AUC/Gini/KS, information value, point-in-time binomial calibration tests, PSI, macro satellite model (unemployment, GDP), baseline/adverse/severe PD and expected loss. | `credit/` |
| **Data layer** | 25 PostgreSQL tables with keys, foreign keys and check constraints; 15+ data-quality rules (CRITICAL holds the run); reconciliation log against the ledger; run-level lineage; reporting views for Power BI. | `data/`, `sql/migrations/` |
| **Engineering** | Installable package, YAML config with env-var expansion and a config hash, structured JSON logging with `run_id`, 33 tests (91% coverage), ruff, mypy, Dockerfile, docker-compose, GitHub Actions CI against Postgres. | repo root |
| **Deployment** | Terraform for AWS: EventBridge Scheduler (weekdays 17:30 Central) triggers an ECS Fargate task; RDS PostgreSQL, S3, Secrets Manager, CloudWatch metric filters and alarms to SNS email. Azure mapping included. | `infra/` |
| **Reporting** | Self-contained HTML risk report with auto-generated commentary, limits, market moves, positions, VaR/ES, backtest, stress, credit, data quality, reconciliation and governance. Streamlit dashboard with seven tabs. | `reporting/`, `dashboards/` |
| **Governance** | Model inventory, limits with amber/red triggers, 18 automated validation tests written every run, model-risk memo with assumptions and limitations. | `governance/`, `config/`, `docs/` |

## The daily run

```mermaid
flowchart LR
  A[Scheduler 17:30 CT] --> B[Ingest landing files]
  B --> C{Data-quality rules}
  C -- critical fail --> H[Run HELD + alert]
  C -- pass --> D[Curve build + valuation + KRD]
  D --> E[Reconcile to ledger]
  E --> F[VaR / ES: hist, param, MC]
  F --> G[Backtest]
  G --> I[Stress scenarios]
  I --> J[Credit PD + macro stress]
  J --> K[Validation tests + limits]
  K --> L[Report + dashboard]
```

One command, one `run_id`. The `risk_run` row stores the git SHA, config hash and code version; every result table is keyed by `run_id`; every input row carries a `load_id`. So any number on the report traces back to its source file, code and settings.

## Results on the sample data (as of 2026-09-25)

| Measure | Value |
|---|---|
| Market value / DV01 / effective duration | $412.7M / $243,875 per bp / 5.91 years |
| 1-day 99% VaR: historical / parametric / Monte Carlo | $3.58M / $3.56M / $3.66M |
| 1-day 97.5% ES (historical) | $3.74M |
| Backtest (250 days, 99%) | 2 exceptions vs 2.5 expected, GREEN, Kupiec p = 0.74 |
| Worst stress | Parallel +200bp: -$46.2M. Worst historical 10 days: -$25.4M |
| PD model | OOT AUC 0.718, Gini 0.437, PSI 0.042 |
| Credit expected loss: baseline / adverse / severe | $13.3M / $47.8M / $169.6M |
| Limits | 5 green, 3 amber (VaR at 90% of limit, worst stress, reconciliation breaks) |

The sample data has three issues planted on purpose: a stale vendor price, a vendor price 0.85 points away from the model, and a $500k quantity break between the book of record and the ledger. The controls catch all three and they show up in the report commentary.

## Quick start

```bash
pip install -e ".[dev,dashboard]"
quantrisk --plain-logs run --as-of 2026-09-25 --with-sample   # SQLite by default
open reports/latest.html
streamlit run dashboards/app.py
make test                                                      # 33 tests
```

On PostgreSQL:

```bash
export QR_DATABASE_URL=postgresql://quantrisk:quantrisk@localhost:5432/quantrisk
quantrisk run --as-of 2026-09-25 --with-sample
psql "$QR_DATABASE_URL" -f sql/migrations/002_reporting_views.sql
psql "$QR_DATABASE_URL" -c "select * from v_daily_risk_summary"
```

With Docker: `docker compose up --build`, then open http://localhost:8501.

Real Treasury curves: set `FRED_API_KEY` and use `quantrisk.ingest.sources.write_treasury_curves`.

## Repository layout

```
config/            base.yaml, scenarios.yaml, limits.yaml, model_inventory.yaml
src/quantrisk/
  ingest/          sample_data.py, sources.py (FRED), dq_rules.py, loader.py
  data/            schema.py (single source for Postgres + SQLite), db.py, reconciliation.py
  fixed_income/    curves.py, bonds.py, krd.py, portfolio.py
  risk/            factors.py (revaluation), var.py, backtest.py
  stress/          engine.py
  credit/          pd_model.py, calibration.py, macro.py, engine.py
  governance/      limits.py, validation.py
  reporting/       report.py, charts.py, templates/report.html.j2
  pipeline.py      the daily run
  cli.py           quantrisk init-db | generate-sample | run | report
dashboards/app.py  Streamlit
sql/migrations/    001_init.sql (generated), 002_reporting_views.sql
tests/             unit + end-to-end
infra/             terraform/aws, azure/README.md
docs/              architecture.md, data_model.md, model_risk_memo.md, runbook.md
```

## Design decisions

- **One revaluation path.** Historical, Monte Carlo and stress all run through the same full-revaluation function on the same 14 risk factors, so differences between methods come from the scenarios, not the pricing.
- **Effective, not modified, duration** drives DV01 and KRD, because MBS cash flows change with rates.
- **TTC PD, PIT testing.** The scorecard is calibrated through the cycle; calibration tests convert to point in time with each year's macro data, since observed defaults are point in time.
- **Reporting never recalculates.** The report, dashboard and Power BI views read persisted results only.
- **Reference data is upserted, inputs are replaced by slice.** Re-running a date is safe and never duplicates rows (tested).

## Limitations

Sample data stands in for licensed vendor feeds. The MBS prepayment model is stylized. Multi-day VaR uses square-root-of-time scaling. Liquidity risk and counterparty risk are not modeled yet. See `docs/model_risk_memo.md` for the full list and compensating controls.

---

Built by Joseph Bidias. MIT licensed.
