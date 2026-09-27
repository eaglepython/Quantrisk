# Runbook: daily risk run

## Normal day

1. 17:30 CT: the scheduler starts the batch, which takes under a minute.
2. By 19:30 CT the run should be `SUCCEEDED`, with the report in `s3://<bucket>/reports/<date>/`. If it isn't, the "run-missing" alarm emails the team.
3. 07:30 CT: open the report. Read the Summary box first. It lists limits needing attention, data issues and monitoring items.

## Status codes

| Status | Meaning | Action |
|---|---|---|
| `SUCCEEDED` | All steps and the report completed | Review the report |
| `HELD` (exit code 2) | A CRITICAL data-quality rule failed. No risk numbers were produced | Check `dq_check_result` for the run, fix or re-deliver the feed, then rerun the same date |
| `FAILED` | Unexpected error | Check the CloudWatch logs filtered by `run_id`, fix, then rerun |

Reruns are safe. Inputs are replaced by slice and reference data is upserted, so rerunning a date never duplicates rows.

```bash
quantrisk run --as-of 2026-09-25          # rerun a date
quantrisk report --run-id R-20260925-xxxx # rebuild a report without recomputing
```

## Common warnings

| Warning | Likely cause | Who |
|---|---|---|
| `price_not_stale` | Vendor did not update an illiquid bond | Pricing team: challenge the vendor or use an evaluated price |
| `model_vs_vendor_price` | Spread basis out of date, or a bad vendor print | Quant risk: check the issuer basis in `ref_instrument` |
| `curve_day_over_day_move` | A real market move, or a bad tick | Compare with a second source before sign-off |
| Reconciliation `BREAK` | Trade booked in one system only, or a late amendment | Middle office: fix the booking and log the resolution in `recon_log` |

## Log search (CloudWatch Logs Insights)

```
fields ts, level, msg, step, seconds
| filter run_id = "R-20260925-xxxxxx"
| sort ts asc
```
