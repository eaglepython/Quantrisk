"""Config-driven stress engine.

Each scenario in config/scenarios.yaml becomes a factor-shock vector and is
applied with full revaluation. P&L is also decomposed by running the rates,
spread and vol legs on their own; any interaction effect shows up only in total.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quantrisk.risk.factors import SPREADS, FactorHistory, Revaluer


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    vector: np.ndarray


def scenario_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_scenarios(cfg: list[dict[str, Any]], curve_tenors: list[float], history: FactorHistory) -> list[Scenario]:
    n_r = len(curve_tenors)
    out = []
    for sc in cfg:
        vec = np.zeros(n_r + len(SPREADS) + 1)
        desc = sc.get("description", "")
        if sc.get("historical_replay") == "worst_10d":
            out.append(Scenario(sc["id"], sc["name"], desc, np.full_like(vec, np.nan)))   # resolved later
            continue
        rates = {float(k): float(v) for k, v in (sc.get("rates") or {}).items()}
        if rates:
            ks = sorted(rates)
            vec[:n_r] = np.interp(curve_tenors, ks, [rates[k] for k in ks])
        for name, bp in (sc.get("spreads") or {}).items():
            vec[n_r + SPREADS.index(name)] = float(bp)
        vec[-1] = float(sc.get("vol", 0.0))
        out.append(Scenario(sc["id"], sc["name"], desc, vec))
    return out


def resolve_worst_window(reval: Revaluer, history: FactorHistory, window: int = 10) -> tuple[np.ndarray, str]:
    ch = history.changes
    csum = np.cumsum(np.vstack([np.zeros(ch.shape[1]), ch]), axis=0)
    windows = csum[window:] - csum[:-window]                       # (N-window+1) x F
    pnl = reval.position_pnl(windows).sum(axis=1)
    i = int(np.argmin(pnl))
    d0, d1 = history.dates[i], history.dates[i + window - 1]
    return windows[i], f"{d0} to {d1}"


def run_stress(reval: Revaluer, scenarios: list[Scenario], history: FactorHistory, n_rates: int) -> pd.DataFrame:
    rows = []
    for sc in scenarios:
        vec = sc.vector
        desc = sc.description
        if np.isnan(vec).any():
            vec, span = resolve_worst_window(reval, history)
            desc = f"{desc} Window: {span}."
            sc.description = desc
            sc.vector = vec
        legs = np.zeros((4, len(vec)))
        legs[0] = vec
        legs[1, :n_rates] = vec[:n_rates]
        legs[2, n_rates:n_rates + len(SPREADS)] = vec[n_rates:n_rates + len(SPREADS)]
        legs[3, -1] = vec[-1]
        pnl = reval.position_pnl(legs)                              # 4 x P
        for j, p in enumerate(reval.positions.itertuples()):
            rows.append({"scenario_id": sc.id, "book_id": p.book_id, "instrument_id": p.instrument_id,
                         "total_pnl": float(pnl[0, j]), "rates_pnl": float(pnl[1, j]),
                         "spread_pnl": float(pnl[2, j]), "vol_pnl": float(pnl[3, j])})
    return pd.DataFrame(rows)
