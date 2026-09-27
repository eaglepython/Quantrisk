import numpy as np
import pytest
from scipy.stats import norm

from quantrisk.governance.limits import status_for
from quantrisk.risk.backtest import christoffersen_independence, kupiec_pof, traffic_light
from quantrisk.risk.var import ewma_covariance, parametric_var_es, simulate_t_scenarios, var_es_from_pnl


def test_historical_var_es_on_known_sample():
    pnl = -np.arange(1, 101, dtype=float)          # losses 1..100
    var, es = var_es_from_pnl(pnl, 0.95)
    assert var == 95
    assert es == pytest.approx(np.mean(np.arange(95, 101)))


def test_es_never_below_var():
    rng = np.random.default_rng(0)
    pnl = rng.standard_t(4, 1000)
    for c in (0.95, 0.975, 0.99):
        v, e = var_es_from_pnl(pnl, c)
        assert e >= v


def test_parametric_matches_closed_form():
    cov = np.array([[4.0, 1.0], [1.0, 9.0]])
    s = np.array([1.0, 2.0])
    var, es, sigma = parametric_var_es(s, cov, 0.99)
    assert sigma == pytest.approx(np.sqrt(4 + 4 * 1 + 4 * 9))
    assert var == pytest.approx(norm.ppf(0.99) * sigma)
    assert es == pytest.approx(sigma * norm.pdf(norm.ppf(0.99)) / 0.01)


def test_t_scenarios_reproduce_covariance_and_are_seeded():
    cov = np.array([[1.0, 0.5], [0.5, 2.0]])
    a = simulate_t_scenarios(cov, 200_000, 5, 1)
    b = simulate_t_scenarios(cov, 200_000, 5, 1)
    assert np.array_equal(a, b)
    assert np.allclose(np.cov(a.T), cov, atol=0.08)


def test_ewma_weights_recent_data_more():
    x = np.vstack([np.zeros((100, 1)), np.ones((5, 1)) * 10])
    assert ewma_covariance(x, 0.94)[0, 0] > np.var(x)


def test_kupiec_accepts_expected_rate_and_rejects_excess():
    assert kupiec_pof(3, 250, 0.01)[1] > 0.05
    assert kupiec_pof(12, 250, 0.01)[1] < 0.01


def test_christoffersen_detects_clustering():
    clustered = np.zeros(250, dtype=int)
    clustered[100:106] = 1
    spread = np.zeros(250, dtype=int)
    spread[[20, 70, 120, 170, 220, 240]] = 1
    assert christoffersen_independence(clustered)[1] < christoffersen_independence(spread)[1]


def test_traffic_light_and_limits():
    assert [traffic_light(x) for x in (4, 5, 10)] == ["GREEN", "AMBER", "RED"]
    assert status_for(90, 80, 100, "above") == "AMBER"
    assert status_for(100, 80, 100, "at_or_above") == "RED"
