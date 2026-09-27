"""Calibration to a long-run central tendency, rating master scale, binomial
calibration tests and population stability (PSI)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import binomtest

from quantrisk.credit.pd_model import PDModel


def calibrate_to_central_tendency(model: PDModel, dev: pd.DataFrame, central_tendency: float) -> float:
    """Find the log-odds offset so the average PD on the development sample equals the
    long-run default rate. Returns and sets model.offset."""
    s = model.score(dev)

    def gap(off: float) -> float:
        return float(np.mean(1 / (1 + np.exp(-(s + off)))) - central_tendency)

    model.offset = float(brentq(gap, -10, 10))
    return model.offset


def assign_grades(pd_values: np.ndarray, master_scale: dict[str, float]) -> np.ndarray:
    names = list(master_scale)
    bounds = np.array([master_scale[g] for g in names])
    idx = np.searchsorted(bounds, pd_values, side="right")
    idx = np.clip(idx, 0, len(names) - 1)
    return np.array(names)[idx]


def binomial_tests(df: pd.DataFrame, grade_col: str, pd_col: str, y_col: str) -> pd.DataFrame:
    """Per-grade two-sided binomial test of observed defaults against mean predicted PD."""
    rows = []
    for g, part in df.groupby(grade_col, sort=False):
        n = len(part)
        k = int(part[y_col].sum())
        p = float(part[pd_col].mean())
        pv = float(binomtest(k, n, p).pvalue) if n else 1.0
        status = "FAIL" if pv < 0.01 else "WATCH" if pv < 0.05 else "PASS"
        rows.append({"grade": g, "n": n, "defaults": k, "observed_dr": k / n if n else 0.0,
                     "predicted_pd": p, "p_value": pv, "status": status})
    return pd.DataFrame(rows)


def psi(expected: pd.Series, actual: pd.Series, categories: list[str]) -> float:
    """Population stability index across categories (grades)."""
    e = expected.value_counts(normalize=True).reindex(categories, fill_value=0) + 1e-4
    a = actual.value_counts(normalize=True).reindex(categories, fill_value=0) + 1e-4
    return float(((a - e) * np.log(a / e)).sum())
