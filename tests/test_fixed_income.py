from datetime import date

import numpy as np
import pytest

from quantrisk.fixed_income.bonds import (
    Bond,
    accrued_interest,
    analytics,
    days_30_360,
    dirty_price,
    yield_to_maturity,
)
from quantrisk.fixed_income.curves import bootstrap, fit_nelson_siegel, key_rate_shapes, shocks_to_grid
from quantrisk.fixed_income.krd import key_rate_durations

from .conftest import AS_OF, PAR, TENORS


def govt(coupon, years, freq=2):
    return Bond("T", "GOVT", coupon, date(AS_OF.year + years, AS_OF.month, AS_OF.day), AS_OF, freq, "ACT/ACT")


def test_bootstrap_reprices_par_inputs(curve):
    for t, r in zip(TENORS, PAR, strict=True):
        if t >= 1:
            assert curve.par_rate(t) == pytest.approx(r, abs=1e-9)


def test_par_bond_prices_at_100(curve):
    for years in (2, 5, 10, 30):
        b = govt(curve.par_rate(years), years)
        assert dirty_price(b, AS_OF, curve) == pytest.approx(100.0, abs=0.01)


def test_flat_curve_zero_coupon_duration_equals_maturity():
    flat = bootstrap(TENORS, [0.04] * len(TENORS))
    # a 1-coupon annual bond with zero coupon behaves like a zero: Macaulay = maturity
    z = Bond("Z", "GOVT", 0.0, date(2036, 9, 25), AS_OF, 1, "ACT/ACT")
    a = analytics(z, AS_OF, flat)
    t = (z.maturity - AS_OF).days / 365.25
    assert a.eff_duration == pytest.approx(t, rel=1e-3)   # continuous-zero effective duration = time


def test_price_falls_when_rates_rise_and_convexity_positive(curve):
    a = analytics(govt(0.042, 10), AS_OF, curve)
    assert a.eff_duration > 7.5
    assert a.convexity > 0


def test_ytm_round_trip(curve):
    b = govt(0.05, 7)
    p = dirty_price(b, AS_OF, curve)
    y = yield_to_maturity(b, AS_OF, p)
    t = np.arange(1, 15) / 2
    cf = np.full(14, 2.5)
    cf[-1] += 100
    assert (cf / (1 + y / 2) ** (2 * t)).sum() == pytest.approx(p, abs=0.02)


def test_krd_sums_to_effective_duration(curve):
    b = govt(0.045, 20)
    k = key_rate_durations(b, AS_OF, curve, [2, 5, 10, 30])
    a = analytics(b, AS_OF, curve)
    assert sum(k.values()) == pytest.approx(a.eff_duration, abs=1e-4)
    assert k[10.0] > k[2.0]


def test_key_rate_shapes_partition_unity(curve):
    shapes = key_rate_shapes([2, 5, 10, 30], curve.tenors)
    assert np.allclose(shapes.sum(axis=0), 1.0)


def test_shocks_to_grid_interpolates_flat_ends(curve):
    g = shocks_to_grid({2: 10, 10: 50}, curve.tenors)
    assert g[0] == pytest.approx(10 / 1e4)
    assert g[-1] == pytest.approx(50 / 1e4)


def test_mbs_negative_convexity_and_shorter_duration(curve):
    mbs = Bond("M", "MBS", 0.06, date(2055, 9, 1), date(2025, 9, 1), 12, "30/360", "MBS", 0, 0)
    a = analytics(mbs, AS_OF, curve, 110)
    assert a.convexity < 0
    assert a.eff_duration < a.mod_duration


def test_accrued_and_30_360():
    assert days_30_360(date(2026, 2, 28), date(2026, 3, 31)) == 33
    corp = Bond("C", "CORP", 0.06, date(2030, 6, 15), date(2025, 6, 15), 2, "30/360", "IG")
    assert accrued_interest(corp, date(2026, 9, 15)) == pytest.approx(1.5, abs=1e-9)   # 3 of 6 months of a 3.0 coupon


def test_nelson_siegel_fits_curve():
    _, rmse = fit_nelson_siegel(TENORS, PAR)
    assert rmse < 5
