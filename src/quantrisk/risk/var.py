"""Value at Risk and Expected Shortfall: historical, parametric (delta-normal) and
Monte Carlo (multivariate Student t, full revaluation).

Sign convention: P&L is positive for gains. VaR and ES are reported as positive
loss amounts. Multi-day horizons use square-root-of-time scaling (a documented
model assumption; see docs/model_risk_memo.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


def var_es_from_pnl(pnl: np.ndarray, confidence: float) -> tuple[float, float]:
    """Empirical VaR (loss quantile) and ES (mean loss at or beyond VaR)."""
    losses = np.sort(-np.asarray(pnl, dtype=float))
    n = len(losses)
    k = int(np.ceil(n * confidence)) - 1
    var = float(losses[k])
    es = float(losses[k:].mean())
    return var, es


def ewma_covariance(changes: np.ndarray, lam: float = 0.94) -> np.ndarray:
    """RiskMetrics EWMA covariance of factor changes (zero-mean), most recent row last."""
    x = np.asarray(changes, dtype=float)
    n = len(x)
    w = lam ** np.arange(n - 1, -1, -1)
    w = w / w.sum()
    return (x * w[:, None]).T @ x


def parametric_var_es(sens: np.ndarray, cov: np.ndarray, confidence: float) -> tuple[float, float, float]:
    """Delta-normal. sens: F vector of USD per factor unit. Returns (VaR, ES, sigma)."""
    sigma = float(np.sqrt(sens @ cov @ sens))
    z = norm.ppf(confidence)
    return z * sigma, sigma * norm.pdf(z) / (1 - confidence), sigma


def simulate_t_scenarios(cov: np.ndarray, n_paths: int, dof: int, seed: int) -> np.ndarray:
    """Multivariate Student-t draws with covariance equal to `cov` (fat tails, same variance)."""
    rng = np.random.default_rng(seed)
    F = cov.shape[0]
    L = np.linalg.cholesky(cov + 1e-12 * np.eye(F))
    z = rng.standard_normal((n_paths, F)) @ L.T
    chi = rng.chisquare(dof, size=n_paths)
    return z * np.sqrt((dof - 2) / chi)[:, None]


@dataclass
class VaRResult:
    method: str
    confidence: float
    horizon_days: int
    var: float
    es: float


def scale(res: tuple[float, float], horizon: int) -> tuple[float, float]:
    f = float(np.sqrt(horizon))
    return res[0] * f, res[1] * f
