"""Limit monitoring: compare today's metrics with amber and red thresholds."""

from __future__ import annotations

from typing import Any

import pandas as pd


def status_for(value: float, amber: float, red: float, direction: str) -> str:
    if direction == "at_or_above":
        return "RED" if value >= red else "AMBER" if value >= amber else "GREEN"
    return "RED" if value > red else "AMBER" if value > amber else "GREEN"


def check_limits(metrics: dict[str, float], limits_cfg: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for lim in limits_cfg["limits"]:
        m = lim["metric"]
        if m not in metrics:
            continue
        v = float(metrics[m])
        rows.append({
            "limit_id": lim["id"], "description": lim["description"], "value": v,
            "amber": float(lim["amber"]), "red": float(lim["red"]),
            "utilization": v / float(lim["red"]) if lim["red"] else 0.0,
            "status": status_for(v, float(lim["amber"]), float(lim["red"]), lim.get("direction", "above")),
        })
    return pd.DataFrame(rows)
