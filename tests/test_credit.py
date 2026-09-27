import numpy as np
import pandas as pd
import pytest

from quantrisk.credit.calibration import assign_grades, psi
from quantrisk.credit.macro import Satellite, fit_satellite, inv_logit, logit, scenario_pd
from quantrisk.credit.pd_model import discrimination, fit
from quantrisk.ingest.sample_data import generate_obligors

MS = {"AAA": 0.0003, "AA": 0.001, "A": 0.003, "BBB": 0.01, "BB": 0.03, "B": 0.10, "CCC": 1.0}


@pytest.fixture(scope="module")
def panel():
    return generate_obligors(7)


def test_pd_model_discriminates(panel):
    dev = panel[(panel.year <= 2020)].dropna(subset=["default_flag"])
    m = fit(dev)
    metrics = discrimination(dev.default_flag.astype(int), m.pd(dev))
    assert metrics["auc"] > 0.70
    assert metrics["gini"] == pytest.approx(2 * metrics["auc"] - 1)
    coef = m.coefficients()
    assert coef["leverage"] > 0 and coef["log_coverage"] < 0      # signs match credit intuition


def test_grades_follow_master_scale():
    g = assign_grades(np.array([0.0001, 0.0005, 0.002, 0.005, 0.02, 0.05, 0.5]), MS)
    assert list(g) == ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]


def test_psi_zero_for_identical_and_positive_for_shift():
    a = pd.Series(["A"] * 50 + ["BBB"] * 50)
    b = pd.Series(["A"] * 20 + ["BBB"] * 80)
    assert psi(a, a, ["A", "BBB"]) == pytest.approx(0, abs=1e-9)
    assert psi(a, b, ["A", "BBB"]) > 0.1


def test_satellite_recession_raises_pd():
    years = range(2005, 2025)
    rng = np.random.default_rng(1)
    u = pd.Series({y: 4 + 5 * rng.random() for y in years})
    g = pd.Series({y: 3 - 5 * rng.random() for y in years})
    dr = inv_logit(-5 + 0.3 * u - 0.1 * g)
    sat = fit_satellite(pd.Series(dr, index=list(years)), pd.DataFrame({"UNRATE": u, "GDP_GROWTH": g}), (2005, 2024))
    assert sat.b_unemp == pytest.approx(0.3, abs=1e-6)
    assert sat.r2 == pytest.approx(1.0)
    base = np.array([0.01])
    assert scenario_pd(base, sat, 10, -3)[0] > scenario_pd(base, sat, 4, 2)[0]


def test_logit_round_trip():
    p = np.array([0.001, 0.2, 0.9])
    assert np.allclose(inv_logit(logit(p)), p)
    assert isinstance(Satellite(0.0, 0.1, -0.1, 0.9, 5.0, 2.0, 20).shift(6.0, 1.0), float)
