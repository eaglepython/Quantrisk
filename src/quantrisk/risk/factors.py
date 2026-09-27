"""Risk factors and full revaluation of the portfolio under factor scenarios.

Factor set (14): zero-rate changes at the 10 curve tenors, three spread-index
changes (IG, HY, MBS) and one implied-volatility change. Units: bp for rates
and spreads, normal-vol points for volatility.

All three VaR methods and the stress engine share this one revaluation path,
so differences between methods come from the scenarios, not the pricing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from quantrisk.fixed_income.bonds import Bond, price_scenarios
from quantrisk.fixed_income.curves import ZeroCurve, bootstrap

SPREADS = ["IG", "HY", "MBS"]


@dataclass
class FactorHistory:
    dates: list[date]            # dates of the change (t), change = level(t) - level(t-1)
    names: list[str]
    changes: np.ndarray          # N x F


def zero_curve_history(curves: pd.DataFrame) -> tuple[list[date], list[float], np.ndarray]:
    """Bootstrap every day's par curve; return zero rates at the curve tenors (D x T)."""
    piv = curves.pivot(index="as_of_date", columns="tenor_years", values="par_rate").sort_index()
    tenors = [float(t) for t in piv.columns]
    zs = np.empty(piv.shape)
    for i, row in enumerate(piv.values):
        zc = bootstrap(tenors, row)
        zs[i] = zc.zero(np.array(tenors))
    return list(piv.index), tenors, zs


def build_factor_history(curves: pd.DataFrame, spreads: pd.DataFrame, vols: pd.DataFrame) -> tuple[FactorHistory, list[float]]:
    dates, tenors, zs = zero_curve_history(curves)
    sp = spreads.pivot(index="as_of_date", columns="spread_index", values="spread_bp").reindex(dates)[SPREADS].values
    vv = vols.set_index("as_of_date").normal_vol_bp.reindex(dates).values[:, None]
    levels = np.hstack([zs * 1e4, sp, vv])            # bp units
    changes = np.diff(levels, axis=0)
    names = [f"Z{t:g}Y" for t in tenors] + [f"SPR_{s}" for s in SPREADS] + ["VOL"]
    return FactorHistory(dates[1:], names, changes), tenors


class Revaluer:
    """Precomputes base prices and maps factor scenarios to position P&L."""

    def __init__(self, positions: pd.DataFrame, instruments: pd.DataFrame, curve: ZeroCurve,
                 spread_levels: dict[str, float], settle: date, curve_tenors: list[float]):
        self.curve = curve
        self.settle = settle
        self.positions = positions.reset_index(drop=True)
        instr = {r["instrument_id"]: Bond.from_row(r) for r in instruments.to_dict("records")}
        self.bonds = {iid: instr[iid] for iid in self.positions.instrument_id.unique()}
        self.spread_levels = spread_levels
        # map zero changes at curve tenors onto the pricing grid (G x T)
        self.M = _tenor_to_grid(curve.tenors, np.asarray(curve_tenors, dtype=float))
        self.n_rates = len(curve_tenors)
        self.base = {iid: price_scenarios(b, settle, curve, None, self._spread(b))[0] for iid, b in self.bonds.items()}

    def _spread(self, b: Bond) -> float:
        return float(self.spread_levels.get(b.spread_index, 0.0)) if b.spread_index else 0.0

    def instrument_pnl_per_100(self, scen: np.ndarray) -> dict[str, np.ndarray]:
        """Price change per 100 face for each instrument under S x F factor scenarios."""
        scen = np.atleast_2d(scen)
        dz = (scen[:, :self.n_rates] / 1e4) @ self.M.T            # S x G
        out = {}
        for iid, b in self.bonds.items():
            s_idx = SPREADS.index(b.spread_index) if b.spread_index in SPREADS else None
            dspr = scen[:, self.n_rates + s_idx] if s_idx is not None else 0.0
            prices = price_scenarios(b, self.settle, self.curve, dz, self._spread(b) + dspr)
            vol_pnl = b.vega_per_100 * scen[:, -1]
            out[iid] = prices - self.base[iid] + vol_pnl
        return out

    def position_pnl(self, scen: np.ndarray) -> np.ndarray:
        """S x P matrix of USD P&L by position."""
        per100 = self.instrument_pnl_per_100(scen)
        cols = [per100[iid] * face / 100.0 for iid, face in zip(self.positions.instrument_id,
                                                                  self.positions.face_amount, strict=True)]
        return np.column_stack(cols)

    def sensitivities(self, bump: float = 1.0) -> np.ndarray:
        """P x F matrix of USD P&L per +1 unit of each factor (central difference)."""
        F = self.n_rates + len(SPREADS) + 1
        up = np.eye(F) * bump
        pnl = self.position_pnl(np.vstack([up, -up]))              # 2F x P
        return ((pnl[:F] - pnl[F:]) / (2 * bump)).T


def _tenor_to_grid(grid: np.ndarray, tenors: np.ndarray) -> np.ndarray:
    """G x T linear interpolation matrix: a change vector at tenors -> change on grid (flat ends)."""
    G, T = len(grid), len(tenors)
    M = np.zeros((G, T))
    for g, t in enumerate(grid):
        if t <= tenors[0]:
            M[g, 0] = 1
        elif t >= tenors[-1]:
            M[g, -1] = 1
        else:
            i = np.searchsorted(tenors, t, side="right") - 1
            w = (t - tenors[i]) / (tenors[i + 1] - tenors[i])
            M[g, i], M[g, i + 1] = 1 - w, w
    return M
