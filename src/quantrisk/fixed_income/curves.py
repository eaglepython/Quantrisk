"""Yield-curve construction.

Input: a Treasury par curve (semiannual bond-equivalent yields at standard tenors).
Output: a ZeroCurve with continuously-compounded zero rates on a half-year grid.

Method
  1. Linearly interpolate par yields onto a 0.5-year grid out to 30 years.
  2. Bootstrap discount factors: for a par bond with coupon c paying semiannually,
         1 = c/2 * sum_{i<n} DF(t_i) + (1 + c/2) * DF(t_n)
  3. Convert to continuous zero rates z(t) = -ln DF(t) / t.
  4. Between grid points, interpolate linearly on zero rates (flat extrapolation).

Also provides key-rate bump shapes and a Nelson-Siegel fit used as a benchmark.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

GRID = np.concatenate([[0.25], np.arange(0.5, 30.0001, 0.5)])


@dataclass(frozen=True)
class ZeroCurve:
    tenors: np.ndarray       # grid in years, ascending
    zeros: np.ndarray        # continuously compounded zero rates

    def zero(self, t: np.ndarray | float) -> np.ndarray:
        return np.interp(np.asarray(t, dtype=float), self.tenors, self.zeros)

    def df(self, t: np.ndarray | float) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        return np.exp(-self.zero(t) * t)

    def forward(self, t1: float, t2: float) -> float:
        """Continuously compounded forward rate between t1 and t2."""
        return float((self.zero(t2) * t2 - self.zero(t1) * t1) / (t2 - t1))

    def shifted(self, dz: np.ndarray) -> ZeroCurve:
        """New curve with zero rates shifted by dz (decimal) at each grid point."""
        return ZeroCurve(self.tenors, self.zeros + np.asarray(dz, dtype=float))

    def par_rate(self, t: float, freq: int = 2) -> float:
        """Par coupon (bond-equivalent) implied by this curve for maturity t."""
        times = np.arange(1.0 / freq, t + 1e-9, 1.0 / freq)
        dfs = self.df(times)
        return float(freq * (1.0 - dfs[-1]) / dfs.sum())

    def interp_weights(self, t: np.ndarray) -> np.ndarray:
        """Matrix W (len(t) x len(grid)) such that zero(t) = W @ zeros. Used to
        reprice many scenarios at once: Z_scen (S x G) @ W.T gives S x len(t)."""
        t = np.clip(np.asarray(t, dtype=float), self.tenors[0], self.tenors[-1])
        idx = np.clip(np.searchsorted(self.tenors, t, side="right") - 1, 0, len(self.tenors) - 2)
        t0, t1 = self.tenors[idx], self.tenors[idx + 1]
        w1 = (t - t0) / (t1 - t0)
        W = np.zeros((len(t), len(self.tenors)))
        W[np.arange(len(t)), idx] = 1.0 - w1
        W[np.arange(len(t)), idx + 1] = w1
        return W


def bootstrap(par_tenors: Sequence[float], par_rates: Sequence[float], grid: np.ndarray = GRID) -> ZeroCurve:
    """Bootstrap a zero curve from semiannual par yields."""
    pt = np.asarray(par_tenors, dtype=float)
    pr = np.asarray(par_rates, dtype=float)
    order = np.argsort(pt)
    pt, pr = pt[order], pr[order]
    par_on_grid = np.interp(grid, pt, pr)
    dfs = np.empty_like(grid)
    annuity = 0.0  # running sum of discount factors on prior semiannual coupon dates
    for i, t in enumerate(grid):
        c = par_on_grid[i]
        if t < 0.5 - 1e-9:
            # bills: single payment, bond-equivalent yield
            dfs[i] = (1.0 + c / 2.0) ** (-2.0 * t)
            continue
        dfs[i] = (1.0 - c / 2.0 * annuity) / (1.0 + c / 2.0)
        annuity += dfs[i]
    zeros = -np.log(dfs) / grid
    return ZeroCurve(grid.copy(), zeros)


def key_rate_shapes(key_tenors: Sequence[float], grid: np.ndarray = GRID) -> np.ndarray:
    """Triangular bump shapes (K x G). Each column sums to 1, so bumping every key
    rate by 1bp equals a 1bp parallel shift and KRDs add up to effective duration."""
    k = np.asarray(sorted(key_tenors), dtype=float)
    shapes = np.zeros((len(k), len(grid)))
    for j, t in enumerate(grid):
        if t <= k[0]:
            shapes[0, j] = 1.0
        elif t >= k[-1]:
            shapes[-1, j] = 1.0
        else:
            i = np.searchsorted(k, t, side="right") - 1
            w = (t - k[i]) / (k[i + 1] - k[i])
            shapes[i, j] = 1.0 - w
            shapes[i + 1, j] = w
    return shapes


def shocks_to_grid(key_shocks_bp: dict[float, float], grid: np.ndarray = GRID) -> np.ndarray:
    """Interpolate key-tenor shocks (bp) onto the grid, flat beyond the ends. Returns decimals."""
    if not key_shocks_bp:
        return np.zeros_like(grid)
    ks = sorted((float(k), float(v)) for k, v in key_shocks_bp.items())
    return np.interp(grid, [k for k, _ in ks], [v for _, v in ks]) / 1e4


# ----------------------------------------------------------------- Nelson-Siegel
def nelson_siegel(t: np.ndarray, b0: float, b1: float, b2: float, lam: float) -> np.ndarray:
    t = np.maximum(np.asarray(t, dtype=float), 1e-6)
    x = t / lam
    f1 = (1 - np.exp(-x)) / x
    return b0 + b1 * f1 + b2 * (f1 - np.exp(-x))


def fit_nelson_siegel(tenors: Sequence[float], rates: Sequence[float]) -> tuple[np.ndarray, float]:
    """Fit level/slope/curvature. Returns (params, RMSE in bp)."""
    t = np.asarray(tenors, dtype=float)
    r = np.asarray(rates, dtype=float)

    def resid(p: np.ndarray) -> np.ndarray:
        return nelson_siegel(t, *p) - r

    x0 = np.array([r[-1], r[0] - r[-1], 0.0, 2.0])
    sol = least_squares(resid, x0, bounds=([-0.1, -0.2, -0.3, 0.1], [0.3, 0.2, 0.3, 30.0]))
    rmse_bp = float(np.sqrt(np.mean(sol.fun ** 2)) * 1e4)
    return sol.x, rmse_bp
