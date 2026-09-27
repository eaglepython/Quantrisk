"""Bond cash flows, pricing and sensitivities.

Pricing convention: dirty price per 100 face =
    sum_i CF_i * exp(-(z(t_i) + s) * t_i)
where z is the Treasury zero curve and s is a continuous spread (z-spread for
corporates, OAS-style spread for MBS). Clean price = dirty price - accrued.

Two cash-flow types:
  * Fixed-rate bullet (GOVT, CORP): deterministic coupons plus principal.
  * Agency MBS pass-through: level-pay mortgage pool with an S-curve prepayment
    model driven by the refinancing incentive, so cash flows change with rates.
    That is what produces negative convexity.

All pricing functions are vectorized across scenarios so historical and Monte
Carlo VaR can fully revalue thousands of curve scenarios at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from dateutil.relativedelta import relativedelta
from scipy.optimize import brentq

from quantrisk.fixed_income.curves import ZeroCurve

DAYS_PER_YEAR = 365.25
MBS_WAC_OVER_COUPON = 0.0075       # pool WAC above pass-through coupon
MBS_PRIMARY_SPREAD = 0.0200        # mortgage rate = 10y Treasury zero + spread


@dataclass(frozen=True)
class Bond:
    instrument_id: str
    asset_class: str          # GOVT / CORP / MBS
    coupon: float
    maturity: date
    issue_date: date
    frequency: int
    day_count: str
    spread_index: str | None = None
    spread_basis_bp: float = 0.0
    vega_per_100: float = 0.0

    @staticmethod
    def from_row(r: dict[str, Any]) -> Bond:
        def d(x: Any) -> date:
            return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])
        si = r.get("spread_index")
        return Bond(
            instrument_id=str(r["instrument_id"]), asset_class=str(r["asset_class"]),
            coupon=float(r["coupon"]), maturity=d(r["maturity_date"]), issue_date=d(r["issue_date"]),
            frequency=int(r["frequency"]), day_count=str(r["day_count"]),
            spread_index=None if si in (None, "", "None") or (isinstance(si, float) and np.isnan(si)) else str(si),
            spread_basis_bp=float(r.get("spread_basis_bp") or 0.0),
            vega_per_100=float(r.get("vega_per_100") or 0.0),
        )


# ----------------------------------------------------------------- day counts
def days_30_360(d1: date, d2: date) -> int:
    day1 = min(d1.day, 30)
    day2 = min(d2.day, 30) if day1 == 30 else d2.day
    return 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + (day2 - day1)


def coupon_dates(bond: Bond, settle: date) -> tuple[list[date], date]:
    """Future coupon dates (> settle) and the previous coupon date (<= settle)."""
    months = 12 // bond.frequency
    dates: list[date] = []
    k = 0
    d = bond.maturity
    while d > settle:
        dates.append(d)
        k += 1
        d = bond.maturity - relativedelta(months=months * k)
    return sorted(dates), d


def accrued_interest(bond: Bond, settle: date) -> float:
    if bond.asset_class == "MBS":
        return bond.coupon / 12 * 100 * (settle.day - 1) / 30.0
    future, prev = coupon_dates(bond, settle)
    if not future:
        return 0.0
    nxt = future[0]
    cpn = bond.coupon / bond.frequency * 100
    if bond.day_count.upper() in ("30/360", "30_360"):
        frac = days_30_360(prev, settle) / (360 / bond.frequency)
    else:  # ACT/ACT (ICMA)
        frac = (settle - prev).days / max((nxt - prev).days, 1)
    return cpn * frac


def fixed_cashflows(bond: Bond, settle: date) -> tuple[np.ndarray, np.ndarray]:
    """Times (years from settle) and cash flows per 100 face."""
    future, _ = coupon_dates(bond, settle)
    cpn = bond.coupon / bond.frequency * 100
    t = np.array([(d - settle).days / DAYS_PER_YEAR for d in future])
    cf = np.full(len(future), cpn)
    if len(cf):
        cf[-1] += 100.0
    return t, cf


# ----------------------------------------------------------------- MBS
def mbs_cpr(incentive: np.ndarray) -> np.ndarray:
    """S-curve prepayment speed (annual CPR) from refinancing incentive (decimal)."""
    x = incentive * 100.0
    return 0.06 + 0.30 / (1.0 + np.exp(-1.2 * (x - 1.0)))


def mbs_cashflows(bond: Bond, settle: date, mortgage_rate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Monthly cash flows per 100 current face for each scenario mortgage rate.

    Returns t (M,) and CF (S x M)."""
    mortgage_rate = np.atleast_1d(np.asarray(mortgage_rate, dtype=float))
    n = max(1, (bond.maturity.year - settle.year) * 12 + (bond.maturity.month - settle.month))
    wac = bond.coupon + MBS_WAC_OVER_COUPON
    r = wac / 12.0
    smm = 1.0 - (1.0 - mbs_cpr(wac - mortgage_rate)) ** (1.0 / 12.0)     # (S,)
    S = len(mortgage_rate)
    bal = np.full(S, 100.0)
    cfs = np.zeros((S, n))
    for m in range(n):
        remaining = n - m
        pmt = bal * r / (1.0 - (1.0 + r) ** (-remaining))
        sched_prin = pmt - bal * r
        prepay = smm * (bal - sched_prin)
        interest = bal * bond.coupon / 12.0
        cfs[:, m] = interest + sched_prin + prepay
        bal = bal - sched_prin - prepay
    first = date(settle.year, settle.month, 25)
    if first <= settle:
        first = first + relativedelta(months=1)
    t = np.array([((first + relativedelta(months=m)) - settle).days / DAYS_PER_YEAR for m in range(n)])
    return t, cfs


# ----------------------------------------------------------------- pricing
def _spread_decimal(bond: Bond, index_spread_bp: np.ndarray | float) -> np.ndarray:
    if bond.asset_class == "GOVT":
        return np.zeros_like(np.atleast_1d(np.asarray(index_spread_bp, dtype=float)))
    return (np.atleast_1d(np.asarray(index_spread_bp, dtype=float)) + bond.spread_basis_bp) / 1e4


def price_scenarios(bond: Bond, settle: date, curve: ZeroCurve, dz: np.ndarray | None = None,
                    index_spread_bp: np.ndarray | float = 0.0) -> np.ndarray:
    """Dirty price per 100 under S scenarios.

    dz: (S x G) zero-rate shifts in decimals on the curve grid (None = base).
    index_spread_bp: (S,) or scalar spread-index level in bp.
    """
    if dz is None:
        dz = np.zeros((1, len(curve.tenors)))
    dz = np.atleast_2d(dz)
    S = dz.shape[0]
    zgrid = curve.zeros[None, :] + dz                          # S x G
    spread = np.broadcast_to(_spread_decimal(bond, index_spread_bp), (S,))
    if bond.asset_class == "MBS":
        w10 = curve.interp_weights(np.array([10.0]))           # 1 x G
        mort = (zgrid @ w10.T)[:, 0] + MBS_PRIMARY_SPREAD
        t, cf = mbs_cashflows(bond, settle, mort)             # (M,), S x M
    else:
        t, cf1 = fixed_cashflows(bond, settle)
        cf = np.broadcast_to(cf1, (S, len(t)))
    W = curve.interp_weights(t)                                # C x G
    zt = zgrid @ W.T                                           # S x C
    disc = np.exp(-(zt + spread[:, None]) * t[None, :])
    return np.asarray((cf * disc).sum(axis=1))


def dirty_price(bond: Bond, settle: date, curve: ZeroCurve, index_spread_bp: float = 0.0) -> float:
    return float(price_scenarios(bond, settle, curve, None, index_spread_bp)[0])


def yield_to_maturity(bond: Bond, settle: date, dirty: float, curve: ZeroCurve | None = None) -> float:
    """Street-style yield (compounded at the bond's frequency) that reproduces the dirty price."""
    if bond.asset_class == "MBS":
        assert curve is not None, "MBS yield needs the curve to project cash flows"
        mort = curve.zero(10.0) + MBS_PRIMARY_SPREAD
        t, cf2 = mbs_cashflows(bond, settle, np.array([mort]))
        cf = cf2[0]
        f = 12
    else:
        t, cf = fixed_cashflows(bond, settle)
        f = bond.frequency

    def pv(y: float) -> float:
        return float((cf / (1 + y / f) ** (f * t)).sum() - dirty)

    return float(brentq(pv, -0.05, 1.0, xtol=1e-12))


def modified_duration_from_yield(bond: Bond, settle: date, ytm: float, curve: ZeroCurve | None = None) -> float:
    if bond.asset_class == "MBS":
        assert curve is not None
        mort = curve.zero(10.0) + MBS_PRIMARY_SPREAD
        t, cf2 = mbs_cashflows(bond, settle, np.array([mort]))
        cf, f = cf2[0], 12
    else:
        t, cf = fixed_cashflows(bond, settle)
        f = bond.frequency
    pv = cf / (1 + ytm / f) ** (f * t)
    mac = float((t * pv).sum() / pv.sum())
    return mac / (1 + ytm / f)


@dataclass
class Analytics:
    dirty: float
    clean: float
    accrued: float
    ytm: float
    mod_duration: float
    eff_duration: float
    convexity: float
    spread_duration: float


def analytics(bond: Bond, settle: date, curve: ZeroCurve, index_spread_bp: float = 0.0, bump_bp: float = 1.0) -> Analytics:
    """Price and first/second-order sensitivities by full revaluation.

    Effective duration and convexity use parallel zero-curve bumps of +/- bump_bp
    (effective measures capture MBS prepayment behaviour, unlike modified duration).
    """
    dy = bump_bp / 1e4
    G = len(curve.tenors)
    dz = np.vstack([np.zeros(G), np.full(G, dy), np.full(G, -dy)])
    p0, pu, pd_ = price_scenarios(bond, settle, curve, dz, index_spread_bp)
    # use a bigger bump for convexity so it is not swamped by rounding
    dyc = 25 / 1e4
    pcu, pcd = price_scenarios(bond, settle, curve, np.vstack([np.full(G, dyc), np.full(G, -dyc)]), index_spread_bp)
    s = np.array([index_spread_bp + bump_bp, index_spread_bp - bump_bp])
    su, sd = price_scenarios(bond, settle, curve, np.zeros((2, G)), s) if bond.asset_class != "GOVT" else (p0, p0)
    acc = accrued_interest(bond, settle)
    ytm = yield_to_maturity(bond, settle, p0, curve)
    return Analytics(
        dirty=float(p0), clean=float(p0 - acc), accrued=float(acc), ytm=ytm,
        mod_duration=modified_duration_from_yield(bond, settle, ytm, curve),
        eff_duration=float((pd_ - pu) / (2 * p0 * dy)),
        convexity=float((pcu + pcd - 2 * p0) / (p0 * dyc ** 2)),
        spread_duration=float((sd - su) / (2 * p0 * dy)) if bond.asset_class != "GOVT" else 0.0,
    )
