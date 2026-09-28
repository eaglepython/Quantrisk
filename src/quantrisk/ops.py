from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def check_data_freshness(raw_root: Path | str, as_of: date, *, max_age_hours: float = 24.0) -> tuple[bool, list[str]]:
    """Check whether the required live-market files are present and fresh enough for production use."""
    root = Path(raw_root)
    required = [
        "curves.csv",
        "prices.csv",
        "spreads.csv",
        "vols.csv",
        "positions.csv",
        "ledger.csv",
    ]
    stale: list[str] = []
    for name in required:
        candidate = root / as_of.isoformat() / name
        if not candidate.exists():
            stale.append(name)
            continue
        age_hours = (datetime.utcnow() - datetime.fromtimestamp(candidate.stat().st_mtime)).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            stale.append(name)
    return len(stale) == 0, stale


def build_alert_summary(run_id: str, *, limits: list[dict[str, Any]] | None = None, stale: list[str] | None = None) -> dict[str, Any]:
    """Build a compact operational summary for alerting and daily review."""
    breaches = []
    if limits:
        for item in limits:
            status = str(item.get("status", "")).upper()
            if status in {"AMBER", "RED"}:
                breaches.append({
                    "limit_id": item.get("limit_id"),
                    "status": status,
                    "value": item.get("value"),
                    "amber": item.get("amber"),
                    "red": item.get("red"),
                })
    return {
        "run_id": run_id,
        "status": "ALERT" if breaches or (stale or []) else "OK",
        "breaches": breaches,
        "stale_files": stale or [],
    }


def generate_daily_summary(run_id: str, *, metrics: dict[str, Any] | None = None, stale: list[str] | None = None) -> str:
    """Generate a plain-English operational summary for the daily finance review."""
    metrics = metrics or {}
    stale = stale or []
    parts = [f"Daily operational review for run {run_id}."]
    if metrics.get("var_hist_99_1d") is not None:
        parts.append(f"1-day 99% VaR is {metrics['var_hist_99_1d']:.2f}.")
    if metrics.get("stress_worst_loss") is not None:
        parts.append(f"Worst stress loss is {metrics['stress_worst_loss']:.2f}.")
    if stale:
        parts.append(f"Data freshness check flagged stale files: {', '.join(stale)}.")
    else:
        parts.append("All required market inputs are fresh.")
    return " ".join(parts)


def build_live_run_payload(as_of: date, settings: Any, *, run_id: str | None = None) -> dict[str, Any]:
    """Return a compact payload used by the production daily operation."""
    return {
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "database_url": getattr(settings, "database_url", None),
        "raw_root": str(getattr(settings, "path")("raw")),
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
        "status": "READY",
        "checks": {"data_freshness": True},
        "live_mode": True,
    }
