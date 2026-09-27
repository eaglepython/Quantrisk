# Model-Risk Memo: QuantRisk Platform v1.0.0

| | |
|---|---|
| Models covered | FI_PRICING, MR_VAR, STRESS, PD_MODEL (see `config/model_inventory.yaml`) |
| Validation date | 2026-09-15; evidence refreshed by the run of 2026-09-25 |
| Prepared by | Joseph Bidias, model developer (a production review needs an independent validator) |
| Framework | SR 11-7 / OCC 2011-12: conceptual soundness, ongoing monitoring, outcomes analysis |
| Overall rating | **Fit for purpose with conditions** (2 medium findings, 3 low) |

## 1. Executive summary

QuantRisk produces daily valuations, interest-rate sensitivities, VaR and Expected Shortfall, stress losses and credit expected loss for a $413M fixed-income portfolio and a $6.8B loan book. I reviewed each component's theory, implementation, data and outcomes.

The pricing and VaR models perform well. Treasuries reprice within 0.05 points of vendor prices. The 99% VaR backtest shows 2 exceptions in 250 days against 2.5 expected (Basel green, Kupiec p = 0.74, Christoffersen p = 0.86). Historical, parametric and Monte Carlo VaR agree within 3%. The PD model discriminates adequately out of time (AUC 0.718, Gini 0.437, down from 0.502 in development) and is stable (PSI 0.042). Once converted to point in time, it is well calibrated in every grade.

The main conditions are that the MBS prepayment model is stylized, and that the multi-day VaR scaling and the fixed LGD are simplifications that need compensating controls.

## 2. Model descriptions and intended use

**FI_PRICING.** Bootstraps continuously-compounded zero rates from UST par yields on a semiannual grid and interpolates linearly on zero rates. Bullet bonds are priced as the sum of cash flows discounted at zero rate plus a z-spread (spread index plus an issuer basis). Agency MBS pools are priced from level-pay amortization with an S-curve prepayment model: CPR = 6% + 30% / (1 + exp(-1.2 (incentive - 1%))), where incentive = WAC - (10Y zero + 2.00%). Effective duration and convexity come from parallel zero-curve bumps (1bp and 25bp respectively), and key-rate durations from triangular bumps at 2/5/10/30Y. *Use:* valuation for risk only (not official P&L), DV01 limits, hedging analysis.

**MR_VAR.** There are 14 risk factors: zero changes at 10 tenors, 3 spread indices and 1 vol index. Historical simulation applies the last 500 daily factor changes to today's positions with full revaluation. The parametric method is delta-normal, using the EWMA covariance (lambda 0.94) and bump sensitivities. Monte Carlo draws 10,000 multivariate Student-t scenarios (5 degrees of freedom) scaled to the EWMA covariance, with full revaluation. ES is the average loss beyond VaR. *Use:* daily limit monitoring and risk reporting.

**STRESS.** Named scenarios in `config/scenarios.yaml`, expressed as key-tenor rate shocks, spread shocks and vol shocks, with full revaluation and a decomposition into legs. A historical replay searches the loaded history for the worst 10-day window for today's portfolio (currently 2025-03-27 to 2025-04-09, -$25.4M).

**PD_MODEL.** A logistic regression on leverage, ln(interest coverage), ROA, ln(current ratio) and ln(assets), developed on 2005-2020 (48,000 firm-years, 507 defaults) and tested out of time on 2021-2024. It is calibrated to a long-run central tendency of 1.06% and mapped to a 7-grade master scale. A macro satellite regresses logit(annual default rate) on unemployment (+0.325 per point) and GDP growth (-0.151 per point), with R² 0.91. Scenario PDs shift each obligor's log-odds by the scenario's distance from the development-period average (unemployment 6.24%, GDP growth 1.72%). EL = PD x LGD x EAD, with LGD 35/45/65% by seniority.

## 3. Data

Inputs arrive as landing files, are typed against schemas, and are checked by 15+ rules. CRITICAL rules cover missing tenors, rate bounds, unmapped positions, duplicate keys and missing spreads or vol; any failure holds the run with status `HELD` and exit code 2, and this is tested. WARNING rules cover day-over-day moves above 50bp, negative forwards, stale prices, model-versus-vendor price gaps, matured instruments and obligor ranges. Positions and book market values are reconciled to the ledger, and every comparison is logged.

The run of 2026-09-25 flagged a stale vendor price (LWR-32, 4 business days), a model-versus-vendor gap on CTI-36 (0.84 points) and a $500k face break on UST-10Y. All three were planted in the sample data, and all three were caught.

**Limitation.** The current data is synthetic sample data, generated with realistic dynamics and stylized stress episodes. Production use requires licensed vendor prices and a real obligor history.

## 4. Conceptual soundness

| Choice | Rationale | Alternatives considered |
|---|---|---|
| Linear interpolation on zero rates | Transparent, no negative forwards on normal curves, reprices par inputs exactly (tested) | Cubic spline (can oscillate), monotone convex (better forwards, more complex); Nelson-Siegel kept as a benchmark (RMSE 1.4bp) |
| Effective duration for DV01 | Captures option-driven cash-flow changes in MBS | Modified duration understates MBS risk shifts |
| Full revaluation for hist and MC | Captures convexity; delta-only misses 2.0% of the +100bp loss | Delta-gamma (faster, less exact) |
| Student-t MC | Fat tails, matching the empirical distribution | Gaussian MC (understates tails), filtered historical simulation |
| TTC PD with PIT conversion | Stable ratings for limits and pricing, while macro sensitivity is kept for stress | Pure PIT model (volatile grades) |

## 5. Outcomes analysis (run of 2026-09-25)

| Test | Result | Threshold | Status |
|---|---|---|---|
| Par bond reprices to 100 | 99.998 | within 0.01 | PASS |
| KRDs sum to effective duration | max gap 0.0000y | < 0.01y | PASS |
| Treasury model vs vendor | 0.050 pts | < 0.10 | PASS |
| Nelson-Siegel benchmark RMSE | 1.41bp | < 5bp | PASS |
| VaR backtest exceptions | 2 / 250 | Basel green 0 to 4 | PASS |
| Kupiec POF p-value | 0.742 | > 0.05 | PASS |
| Christoffersen independence p-value | 0.857 | > 0.05 | PASS |
| ES at least VaR, all methods | yes | always | PASS |
| MC / historical VaR99 ratio | 1.02 | 0.75 to 1.33 | PASS |
| Delta vs full revaluation, +100bp | 2.0% | < 10% | PASS |
| PD out-of-time AUC | 0.718 | at least 0.70 | PASS |
| Gini deterioration, dev to OOT | 0.065 | < 0.10 | PASS |
| PSI (grade mix) | 0.042 | < 0.10 | PASS |
| PIT binomial tests failing at 1% | 0 of 6 grades | 0 | PASS |
| TTC binomial tests failing | 3 of 6 grades | informational | WATCH |
| Macro satellite R² | 0.91 | at least 0.50 | PASS |

These tests run automatically in every batch and are stored in `model_validation`, so monitoring evidence builds up daily.

## 6. Key assumptions

1. The last 500 business days are a representative sample of one-day market moves.
2. Multi-day VaR scales with the square root of time, which ignores autocorrelation, volatility clustering over the horizon and position changes.
3. Parametric VaR assumes joint normality and linear P&L.
4. Implied-volatility risk affects only MBS, through a static vega per 100 face.
5. Default risk depends on macro conditions through a stable log-odds relationship estimated on 20 annual observations.
6. LGD is fixed by seniority and does not rise in a downturn.

## 7. Limitations and compensating controls

| Limitation | Impact | Compensating control |
|---|---|---|
| Stylized MBS prepayment model (no burnout, turnover, seasonality) | MBS duration and convexity could be off by a year or more in fast-refi regimes | MBS stress legs reported separately; benchmark against vendor OAS analytics before production |
| Historical window blind to crises outside 500 days | VaR understates tail risk in calm periods | Stress suite includes +200bp and historical worst-window replay; ES limit |
| Square-root-of-time scaling | 10-day VaR may be misstated | 10-day figures are informational; limits set on 1-day |
| Fixed LGD | Severe-scenario EL understated when recoveries fall | Add a downturn LGD overlay (finding M2) |
| Thin default data in high grades (0 defaults in AA) | AA/AAA PDs rest on the model's extrapolation and the floor | 3bp PD floor; expert review of top grades |
| Liquidity and counterparty risk not modeled | Exit costs and derivative exposures excluded | Out of scope for v1; roadmap item |

## 8. Findings

| ID | Severity | Finding | Owner | Due |
|---|---|---|---|---|
| M1 | Medium | Benchmark the MBS prepayment model against vendor analytics and add burnout | Quant Risk | 2026-12-15 |
| M2 | Medium | Introduce a downturn-LGD overlay for the adverse and severe scenarios | Credit Risk | 2026-12-15 |
| L1 | Low | Replace sample data with licensed feeds; retest the DQ thresholds on real data | Data Eng | 2027-01-31 |
| L2 | Low | Add filtered historical simulation as a challenger VaR | Market Risk | 2027-03-15 |
| L3 | Low | Document the governance process for overriding a TTC grade | Credit Risk | 2027-03-15 |

## 9. Ongoing monitoring and revalidation triggers

- **Daily:** DQ results, reconciliation breaks, backtest exceptions and limit utilization (automated).
- **Monthly:** PSI, observed versus predicted defaults by grade, and a review of stress scenarios.
- **Revalidate early** if the backtest reaches the AMBER zone for 2 consecutive months, if PSI goes above 0.25, if OOT AUC falls below 0.65, after any change to model code that changes model version, or after a new asset class is added.
