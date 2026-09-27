"""Macroeconomic satellite model and stressed expected loss.

Satellite: logit(annual default rate) = a + bU * unemployment + bG * GDP growth,
estimated by OLS on the observed history. Scenario PDs shift each obligor's
through-the-cycle PD in log-odds by the scenario's distance from long-run average
conditions:  logit(PD_s) = logit(PD_ttc) + bU (U_s - U_avg) + bG (G_s - G_avg).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def logit(p: np.ndarray | float) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def inv_logit(x: np.ndarray | float) -> np.ndarray:
    return 1 / (1 + np.exp(-np.asarray(x, dtype=float)))


@dataclass
class Satellite:
    intercept: float
    b_unemp: float
    b_gdp: float
    r2: float
    u_avg: float
    g_avg: float
    n_years: int

    def shift(self, unemployment: float, gdp_growth: float) -> float:
        return self.b_unemp * (unemployment - self.u_avg) + self.b_gdp * (gdp_growth - self.g_avg)


def fit_satellite(default_rates: pd.Series, macro: pd.DataFrame, avg_years: tuple[int, int]) -> Satellite:
    """default_rates indexed by year; macro has columns UNRATE, GDP_GROWTH indexed by year."""
    df = pd.DataFrame({"dr": default_rates}).join(macro, how="inner").dropna()
    y = logit(df.dr.values)
    X = np.column_stack([np.ones(len(df)), df.UNRATE.values, df.GDP_GROWTH.values])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    r2 = 1 - np.sum((y - fitted) ** 2) / np.sum((y - y.mean()) ** 2)
    avg = macro.loc[avg_years[0]:avg_years[1]]
    return Satellite(float(beta[0]), float(beta[1]), float(beta[2]), float(r2),
                     float(avg.UNRATE.mean()), float(avg.GDP_GROWTH.mean()), len(df))


def scenario_pd(pd_ttc: np.ndarray, sat: Satellite, unemployment: float, gdp_growth: float) -> np.ndarray:
    return inv_logit(logit(pd_ttc) + sat.shift(unemployment, gdp_growth))
