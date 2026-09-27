"""Probability-of-default scorecard: logistic regression on financial ratios.

Features (transformed to be roughly linear in log-odds):
  leverage            debt / assets
  log_coverage        ln(EBIT / interest expense)
  roa                 return on assets
  log_current_ratio   ln(current assets / current liabilities)
  log_assets          firm size

Discrimination metrics: AUC, Gini (= 2 AUC - 1) and Kolmogorov-Smirnov.
Information value per feature is reported as a variable-strength check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["leverage", "log_coverage", "roa", "log_current_ratio", "log_assets"]


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "leverage": df["leverage"].astype(float),
        "log_coverage": np.log(df["interest_coverage"].astype(float).clip(lower=0.05)),
        "roa": df["roa"].astype(float),
        "log_current_ratio": np.log(df["current_ratio"].astype(float).clip(lower=0.05)),
        "log_assets": df["log_assets"].astype(float),
    }, index=df.index)


@dataclass
class PDModel:
    pipeline: Pipeline
    offset: float = 0.0          # calibration shift in log-odds

    def score(self, df: pd.DataFrame) -> np.ndarray:
        """Raw log-odds before calibration."""
        return np.asarray(self.pipeline.decision_function(make_features(df)))

    def pd(self, df: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(self.score(df) + self.offset)))

    def coefficients(self) -> dict[str, float]:
        lr: LogisticRegression = self.pipeline.named_steps["lr"]
        sc: StandardScaler = self.pipeline.named_steps["scale"]
        # coefficients on the original (unscaled) feature units
        raw = lr.coef_[0] / sc.scale_
        return dict(zip(FEATURES, map(float, raw), strict=True))


def fit(dev: pd.DataFrame, C: float = 1.0) -> PDModel:
    X = make_features(dev)
    y = dev["default_flag"].astype(int).values
    pipe = Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression(C=C, max_iter=2000))])
    pipe.fit(X, y)
    return PDModel(pipe)


def discrimination(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    auc = float(roc_auc_score(y, p))
    fpr, tpr, _ = roc_curve(y, p)
    return {"auc": auc, "gini": 2 * auc - 1, "ks": float(np.max(tpr - fpr)), "n": int(len(y)),
            "defaults": int(y.sum())}


def information_value(x: pd.Series, y: pd.Series, bins: int = 10) -> float:
    """IV from decile bins with a small adjustment for empty cells."""
    q = pd.qcut(x.rank(method="first"), bins, labels=False)
    tab = pd.crosstab(q, y)
    good = (tab.get(0, 0) + 0.5) / (tab.get(0, 0).sum() + 0.5)
    bad = (tab.get(1, 0) + 0.5) / (tab.get(1, 0).sum() + 0.5)
    return float(((good - bad) * np.log(good / bad)).sum())
