from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from quantrisk.ops import build_alert_summary, check_data_freshness, generate_daily_summary
from quantrisk.pipeline import run_daily


def run_operational_daily(as_of: date, settings: Any, *, with_real_data: bool = True, no_report: bool = False,
                         no_ai: bool = False) -> str:
    """Execute the production daily cycle using live data and operational checks."""
    raw_root = settings.path("raw")
    fresh, stale = check_data_freshness(raw_root, as_of)
    if not fresh:
        raise RuntimeError(f"Data freshness check failed for {as_of.isoformat()}: {stale}")

    if with_real_data:
        from quantrisk.ingest.sources import generate_live_landing
        generate_live_landing(as_of, raw_root)

    run_id = run_daily(as_of, settings, make_report=not no_report)
    summary = generate_daily_summary(run_id, metrics={}, stale=stale) if not no_ai else "Operational run complete."
    if not no_ai:
        from quantrisk.ai import generate_summary
        summary = generate_summary(f"Summarize the daily risk posture for run {run_id} in plain English.")
    return f"{run_id}\n{summary}"
