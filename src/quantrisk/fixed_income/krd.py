"""Key-rate durations by triangular bumps and full revaluation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np

from quantrisk.fixed_income.bonds import Bond, price_scenarios
from quantrisk.fixed_income.curves import ZeroCurve, key_rate_shapes


def key_rate_durations(bond: Bond, settle: date, curve: ZeroCurve, key_tenors: Sequence[float],
                       index_spread_bp: float = 0.0, bump_bp: float = 1.0) -> dict[float, float]:
    """KRD_k = (P(down_k) - P(up_k)) / (2 * P * dy). Shapes sum to a parallel shift,
    so sum_k KRD_k equals effective duration (tested in CI)."""
    dy = bump_bp / 1e4
    shapes = key_rate_shapes(key_tenors, curve.tenors)          # K x G
    dz = np.vstack([np.zeros(len(curve.tenors)), shapes * dy, -shapes * dy])
    prices = price_scenarios(bond, settle, curve, dz, index_spread_bp)
    p0 = prices[0]
    K = len(key_tenors)
    up, down = prices[1:1 + K], prices[1 + K:]
    return {float(k): float((down[i] - up[i]) / (2 * p0 * dy)) for i, k in enumerate(sorted(key_tenors))}
