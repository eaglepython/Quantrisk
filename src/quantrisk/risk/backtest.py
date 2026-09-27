"""VaR backtesting: exceptions, Basel traffic light, Kupiec POF and Christoffersen
independence tests. Uses hypothetical P&L (today's portfolio revalued on each
historical day), the standard approach when positions change daily."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2

from quantrisk.risk.var import var_es_from_pnl


def rolling_historical_var(pnl: np.ndarray, lookback: int, window: int, confidence: float) -> tuple[np.ndarray, np.ndarray]:
    """For each of the last `window` days, VaR from the preceding `lookback` P&Ls.
    Returns (realized pnl, var) arrays of length window."""
    n = len(pnl)
    if n < lookback + window:
        window = max(0, n - lookback)
    realized, vars_ = [], []
    for t in range(n - window, n):
        v, _ = var_es_from_pnl(pnl[t - lookback:t], confidence)
        realized.append(pnl[t])
        vars_.append(v)
    return np.array(realized), np.array(vars_)


def kupiec_pof(exceptions: int, n: int, p: float) -> tuple[float, float]:
    """Proportion-of-failures likelihood-ratio test. Returns (LR, p-value), chi-square 1 dof."""
    x = exceptions
    phat = x / n if n else 0.0

    def ll(k: float, q: float) -> float:
        return 0.0 if k == 0 else k * np.log(q)

    lr = -2 * (ll(n - x, 1 - p) + ll(x, p)) + 2 * (ll(n - x, 1 - phat) + ll(x, phat))
    lr = max(float(lr), 0.0)
    return lr, float(1 - chi2.cdf(lr, 1))


def christoffersen_independence(hits: np.ndarray) -> tuple[float, float]:
    """Tests whether an exception today makes one tomorrow more likely (clustering)."""
    h = np.asarray(hits, dtype=int)
    a, b = h[:-1], h[1:]
    n00 = int(np.sum((a == 0) & (b == 0)))
    n01 = int(np.sum((a == 0) & (b == 1)))
    n10 = int(np.sum((a == 1) & (b == 0)))
    n11 = int(np.sum((a == 1) & (b == 1)))
    pi0 = n01 / max(n00 + n01, 1)
    pi1 = n11 / max(n10 + n11, 1)
    pi = (n01 + n11) / max(n00 + n01 + n10 + n11, 1)

    def ll(k: int, q: float) -> float:
        return 0.0 if k == 0 or q <= 0 else k * np.log(q)

    l0 = ll(n00 + n10, 1 - pi) + ll(n01 + n11, pi)
    l1 = ll(n00, 1 - pi0) + ll(n01, pi0) + ll(n10, 1 - pi1) + ll(n11, pi1)
    lr = max(float(-2 * (l0 - l1)), 0.0)
    return lr, float(1 - chi2.cdf(lr, 1))


def traffic_light(exceptions: int) -> str:
    """Basel zones for 99% VaR over 250 days."""
    if exceptions <= 4:
        return "GREEN"
    if exceptions <= 9:
        return "AMBER"
    return "RED"


@dataclass
class BacktestSummary:
    n: int
    exceptions: int
    expected: float
    zone: str
    kupiec_lr: float
    kupiec_p: float
    christoffersen_lr: float
    christoffersen_p: float


def summarize(realized: np.ndarray, var: np.ndarray, confidence: float) -> tuple[BacktestSummary, np.ndarray]:
    hits = (-realized > var).astype(int)
    n, x = len(hits), int(hits.sum())
    klr, kp = kupiec_pof(x, n, 1 - confidence)
    clr, cp = christoffersen_independence(hits)
    return BacktestSummary(n, x, n * (1 - confidence), traffic_light(x), klr, kp, clr, cp), hits
