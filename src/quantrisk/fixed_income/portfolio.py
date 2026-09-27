"""Position-level valuation: combines instruments, positions and market data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from quantrisk.fixed_income.bonds import Bond, analytics
from quantrisk.fixed_income.curves import ZeroCurve
from quantrisk.fixed_income.krd import key_rate_durations


def index_spread_for(bond: Bond, spreads: dict[str, float]) -> float:
    return float(spreads.get(bond.spread_index, 0.0)) if bond.spread_index else 0.0


def value_positions(positions: pd.DataFrame, instruments: pd.DataFrame, curve: ZeroCurve,
                    spreads: dict[str, float], settle: date, key_tenors: Sequence[float],
                    bump_bp: float = 1.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (valuation, krd) DataFrames at position level.

    Market value = face * dirty / 100. DV01 = eff_duration * MV * 1e-4 (USD per bp, positive
    means the position loses when rates rise)."""
    instr = {r["instrument_id"]: Bond.from_row(r) for r in instruments.to_dict("records")}
    vals, krds = [], []
    cache: dict[str, tuple] = {}
    for p in positions.to_dict("records"):
        b = instr[p["instrument_id"]]
        if b.instrument_id not in cache:
            s = index_spread_for(b, spreads)
            a = analytics(b, settle, curve, s, bump_bp)
            k = key_rate_durations(b, settle, curve, key_tenors, s, bump_bp)
            cache[b.instrument_id] = (a, k)
        a, k = cache[b.instrument_id]
        face = float(p["face_amount"])
        mv = face * a.dirty / 100.0
        vals.append({
            "book_id": p["book_id"], "instrument_id": b.instrument_id, "face_amount": face,
            "clean_price": a.clean, "dirty_price": a.dirty, "accrued": a.accrued, "market_value": mv,
            "ytm": a.ytm, "mod_duration": a.mod_duration, "eff_duration": a.eff_duration,
            "convexity": a.convexity, "spread_duration": a.spread_duration,
            "dv01": a.eff_duration * mv * 1e-4,
        })
        for tenor, krd in k.items():
            krds.append({"book_id": p["book_id"], "instrument_id": b.instrument_id, "tenor_years": tenor,
                         "krd": krd, "dv01": krd * mv * 1e-4})
    return pd.DataFrame(vals), pd.DataFrame(krds)
