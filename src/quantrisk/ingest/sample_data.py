"""Deterministic sample-data generator.

Produces the raw landing files a production feed would deliver, so the whole
platform runs end to end without licensed vendor data:

  instruments.csv   bond terms (fictional issuers)
  curves.csv        daily UST par curve history (factor model with fat tails,
                    volatility clustering and stylized stress episodes)
  spreads.csv       daily IG / HY / MBS spread indices
  vols.csv          daily normal implied volatility index
  prices.csv        vendor evaluated prices for the as-of date
  positions.csv     end-of-day holdings from the book of record
  ledger.csv        accounting ledger balances for reconciliation
  macro.csv         annual unemployment and real GDP growth (approximate US history)
  obligors.csv      corporate borrower panel 2005 onward with default outcomes

Deliberate data issues are planted so the controls have something to find:
one stale vendor price, one vendor price far from model, and one ledger break.
Replace any file with a real extract (for example FRED DGS2/DGS10, UNRATE) and
the pipeline runs unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quantrisk.fixed_income.bonds import Bond, accrued_interest, dirty_price
from quantrisk.fixed_income.curves import bootstrap

CURVE_TENORS = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
START_PAR = [0.0455, 0.0470, 0.0470, 0.0440, 0.0420, 0.0400, 0.0395, 0.0385, 0.0410, 0.0395]
END_PAR = [0.0430, 0.0420, 0.0405, 0.0385, 0.0380, 0.0385, 0.0400, 0.0420, 0.0465, 0.0475]
SPREAD_START = {"IG": 130.0, "HY": 480.0, "MBS": 150.0}
SPREAD_END = {"IG": 95.0, "HY": 320.0, "MBS": 110.0}

# Stylized stress episodes: (start, n_days, level_bp_per_day, slope_bp_per_day, IG, HY, MBS, vol per day)
EPISODES = [
    ("2023-03-09", 6, -12.0, 6.0, 4.0, 14.0, 5.0, 6.0),   # regional-bank stress: rally, steepening, wider spreads
    ("2023-09-25", 15, 3.0, -1.0, 0.5, 2.0, 0.8, 0.8),    # long-end sell-off
    ("2024-08-02", 3, -9.0, 4.0, 3.0, 15.0, 3.0, 5.0),    # growth scare
    ("2025-04-03", 5, 4.0, -3.0, 7.0, 32.0, 6.0, 7.0),    # tariff shock: spreads blow out
]

INSTRUMENTS = [
    # id, description, class, issuer, sector, rating, spread_index, coupon, issue, maturity, freq, day_count, basis_bp, vega
    ("UST-2Y", "UST 3.875% 08/31/2028", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.03875, "2026-08-31", "2028-08-31", 2, "ACT/ACT", 0, 0),
    ("UST-3Y", "UST 3.750% 09/15/2029", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.03750, "2026-09-15", "2029-09-15", 2, "ACT/ACT", 0, 0),
    ("UST-5Y", "UST 3.875% 08/31/2031", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.03875, "2026-08-31", "2031-08-31", 2, "ACT/ACT", 0, 0),
    ("UST-7Y", "UST 4.000% 08/31/2033", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.04000, "2026-08-31", "2033-08-31", 2, "ACT/ACT", 0, 0),
    ("UST-10Y", "UST 4.250% 08/15/2036", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.04250, "2026-08-15", "2036-08-15", 2, "ACT/ACT", 0, 0),
    ("UST-20Y", "UST 4.625% 08/15/2046", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.04625, "2026-08-15", "2046-08-15", 2, "ACT/ACT", 0, 0),
    ("UST-30Y", "UST 4.750% 08/15/2056", "GOVT", "US Treasury", "Sovereign", "AA+", None, 0.04750, "2026-08-15", "2056-08-15", 2, "ACT/ACT", 0, 0),
    ("NWU-33", "Northwind Utilities 5.10% 2033", "CORP", "Northwind Utilities", "Utilities", "A", "IG", 0.05100, "2023-06-15", "2033-06-15", 2, "30/360", -10, 0),
    ("CTI-36", "Contoso Industrial 5.35% 2036", "CORP", "Contoso Industrial", "Industrials", "BBB", "IG", 0.05350, "2026-03-01", "2036-03-01", 2, "30/360", 25, 0),
    ("FBH-31", "Fabrikam Health 4.90% 2031", "CORP", "Fabrikam Health", "Health Care", "A", "IG", 0.04900, "2024-05-15", "2031-05-15", 2, "30/360", -15, 0),
    ("TSE-31", "Tailspin Energy 6.875% 2031", "CORP", "Tailspin Energy", "Energy", "BB", "HY", 0.06875, "2024-02-01", "2031-02-01", 2, "30/360", -60, 0),
    ("LWR-32", "Litware Retail 7.75% 2032", "CORP", "Litware Retail", "Consumer", "B", "HY", 0.07750, "2025-07-15", "2032-07-15", 2, "30/360", 120, 0),
    ("MBS-50", "Agency MBS 30Y 5.0% pool", "MBS", "Agency", "Mortgage", "AA+", "MBS", 0.05000, "2025-09-01", "2055-09-01", 12, "30/360", 0, -0.030),
    ("MBS-60", "Agency MBS 30Y 6.0% pool", "MBS", "Agency", "Mortgage", "AA+", "MBS", 0.06000, "2025-09-01", "2055-09-01", 12, "30/360", 0, -0.045),
]

POSITIONS = {
    "RATES": {"UST-2Y": 60e6, "UST-3Y": 30e6, "UST-5Y": 45e6, "UST-7Y": 25e6, "UST-10Y": 60e6,
              "UST-20Y": 15e6, "UST-30Y": 25e6},
    "CREDIT": {"NWU-33": 20e6, "CTI-36": 25e6, "FBH-31": 15e6, "TSE-31": 12e6, "LWR-32": 8e6,
               "MBS-50": 40e6, "MBS-60": 30e6},
}

# Approximate US annual averages (unemployment %, real GDP growth %). Sample data:
# replace with FRED UNRATE and GDPC1 in production.
MACRO = {
    2005: (5.1, 3.5), 2006: (4.6, 2.8), 2007: (4.6, 2.0), 2008: (5.8, 0.1), 2009: (9.3, -2.6),
    2010: (9.6, 2.7), 2011: (8.9, 1.6), 2012: (8.1, 2.3), 2013: (7.4, 2.1), 2014: (6.2, 2.5),
    2015: (5.3, 2.9), 2016: (4.9, 1.8), 2017: (4.4, 2.5), 2018: (3.9, 3.0), 2019: (3.7, 2.5),
    2020: (8.1, -2.2), 2021: (5.4, 6.1), 2022: (3.6, 2.5), 2023: (3.6, 2.9), 2024: (4.0, 2.8),
    2025: (4.2, 1.9),
}

SECTORS = ["Industrials", "Consumer", "Technology", "Health Care", "Energy", "Utilities", "Materials", "Real Estate"]


def business_days(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, end)]


def _ns_loadings(t: np.ndarray, lam: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    x = t / lam
    slope = (1 - np.exp(-x)) / x
    curv = slope - np.exp(-x)
    return slope, curv


def generate_market_history(as_of: date, start: date, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = business_days(start, as_of)
    n = len(days)
    t = np.array(CURVE_TENORS)
    slope_l, curv_l = _ns_loadings(t)
    # GARCH-like variance multiplier
    h = np.ones(n)
    z = rng.standard_t(5, size=(n, 3)) / np.sqrt(5 / 3)
    for i in range(1, n):
        h[i] = 0.05 + 0.90 * h[i - 1] + 0.05 * (z[i - 1, 0] ** 2)
    sig = np.sqrt(h)
    dL = 5.0 * sig * z[:, 0]
    dS = 3.0 * sig * z[:, 1]
    dC = 2.5 * sig * z[:, 2]
    sp_z = rng.standard_t(5, size=(n, 3)) / np.sqrt(5 / 3)
    corr = np.linalg.cholesky(np.array([[1, .7, .6], [.7, 1, .5], [.6, .5, 1]]))
    sp_shock = (sp_z @ corr.T) * sig[:, None] * np.array([1.4, 7.0, 1.8]) - 0.15 * dL[:, None] * np.array([0.2, 1.0, 0.3])
    dvol = 1.2 * sig * rng.standard_t(5, size=n) / np.sqrt(5 / 3) + 0.08 * np.abs(dL)
    idx = {d: i for i, d in enumerate(days)}
    for s, k, lv, sl, ig, hy, mb, vv in EPISODES:
        d0 = date.fromisoformat(s)
        if d0 not in idx:
            continue
        i0 = idx[d0]
        for j in range(i0, min(n, i0 + k)):
            dL[j] += lv
            dS[j] += sl
            sp_shock[j] += np.array([ig, hy, mb])
            dvol[j] += vv
    dcurve = (dL[:, None] + dS[:, None] * slope_l[None, :] * -1 + dC[:, None] * curv_l[None, :]) / 1e4
    dcurve[0] = 0
    # mean-reverting deviation around a straight line from the start curve to the end curve,
    # so daily moves look like a random walk but levels stay in a realistic range
    w = (np.arange(n) / (n - 1))[:, None]
    anchor = np.array(START_PAR)[None, :] * (1 - w) + np.array(END_PAR)[None, :] * w
    dev = np.zeros_like(dcurve)
    for i in range(1, n):
        dev[i] = 0.99 * dev[i - 1] + dcurve[i]
    dev -= dev[-1][None, :] * w
    path = anchor + dev
    curves = pd.DataFrame(
        [(days[i], t[j], round(float(path[i, j]), 6)) for i in range(n) for j in range(len(t))],
        columns=["as_of_date", "tenor_years", "par_rate"])
    curves["curve_id"] = "UST_PAR"
    curves["source"] = "SAMPLE_TREASURY"

    sp_anchor = (np.array([SPREAD_START[k] for k in ("IG", "HY", "MBS")])[None, :] * (1 - w)
                 + np.array([SPREAD_END[k] for k in ("IG", "HY", "MBS")])[None, :] * w)
    sp_dev = np.zeros_like(sp_shock)
    for i in range(1, n):
        sp_dev[i] = 0.99 * sp_dev[i - 1] + sp_shock[i]
    sp_dev -= sp_dev[-1][None, :] * w
    sp_path = np.maximum(sp_anchor + sp_dev, 20.0)
    spreads = pd.DataFrame(
        [(days[i], k, round(float(sp_path[i, j]), 2)) for i in range(n) for j, k in enumerate(("IG", "HY", "MBS"))],
        columns=["as_of_date", "spread_index", "spread_bp"])
    spreads["source"] = "SAMPLE_INDEX"

    vol = 110 + np.cumsum(dvol)
    vol += (85 - vol[-1]) * (np.arange(n) / (n - 1))
    vols = pd.DataFrame({"as_of_date": days, "vol_index": "USD_SWPN_NVOL", "normal_vol_bp": np.round(vol, 2),
                         "source": "SAMPLE_VOL"})
    return curves, spreads, vols


def generate_obligors(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    n_firms = 3000
    years = sorted(MACRO)
    ids = np.array([f"OB{i:05d}" for i in range(n_firms)])
    next_id = n_firms
    state = rng.standard_normal((n_firms, 5))
    sector = rng.choice(SECTORS, size=n_firms)
    rows = []
    for y in years:
        U, G = MACRO[y]
        state = 0.85 * state + np.sqrt(1 - 0.85 ** 2) * rng.standard_normal(state.shape)
        lev = np.clip(0.44 + 0.19 * state[:, 0], 0.02, 1.2)
        cov = np.clip(np.exp(np.log(4.0) + 0.8 * state[:, 1] - 0.10 * (U - 5.8)), 0.2, 60)
        roa = 0.045 + 0.05 * state[:, 2] - 0.004 * (U - 5.8)
        cr = np.exp(np.log(1.5) + 0.35 * state[:, 3])
        size = 6.2 + 1.4 * state[:, 4]
        x = (-4.3 + 3.2 * (lev - 0.45) - 0.55 * np.log(cov) - 7.0 * (roa - 0.04) - 0.6 * np.log(cr)
             - 0.18 * (size - 6.0) + 0.30 * (U - 5.8) - 0.10 * (G - 2.0))
        pd_true = 1 / (1 + np.exp(-x))
        dflt = (rng.random(n_firms) < pd_true).astype(int)
        observed = y < max(years)
        ead = np.round(np.exp(np.log(1.5e6) + 0.9 * rng.standard_normal(n_firms)).clip(1e5, 25e6), -3)
        seniority = rng.choice(["SENIOR_SECURED", "SENIOR_UNSECURED", "SUBORDINATED"], size=n_firms, p=[.35, .5, .15])
        for i in range(n_firms):
            rows.append((ids[i], y, sector[i], round(float(lev[i]), 4), round(float(cov[i]), 3), round(float(roa[i]), 4),
                         round(float(cr[i]), 3), round(float(size[i]), 3), int(dflt[i]) if observed else None,
                         float(ead[i]) if not observed else None, seniority[i] if not observed else None))
        # replace defaulted firms with new entrants
        for i in np.where(dflt == 1)[0]:
            ids[i] = f"OB{next_id:05d}"
            next_id += 1
            state[i] = rng.standard_normal(5)
            sector[i] = rng.choice(SECTORS)
    return pd.DataFrame(rows, columns=["obligor_id", "year", "sector", "leverage", "interest_coverage", "roa",
                                       "current_ratio", "log_assets", "default_flag", "ead", "seniority"])


def generate(as_of: date, out_dir: str | Path, seed: int = 7, history_start: date = date(2023, 1, 2)) -> Path:
    """Write a full raw landing folder for `as_of` and return its path."""
    out = Path(out_dir) / as_of.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed + 2)

    instr = pd.DataFrame(INSTRUMENTS, columns=["instrument_id", "description", "asset_class", "issuer", "sector",
                                               "rating", "spread_index", "coupon", "issue_date", "maturity_date",
                                               "frequency", "day_count", "spread_basis_bp", "vega_per_100"])
    instr["currency"] = "USD"
    instr.to_csv(out / "instruments.csv", index=False)

    curves, spreads, vols = generate_market_history(as_of, history_start, seed)
    curves.to_csv(out / "curves.csv", index=False)
    spreads.to_csv(out / "spreads.csv", index=False)
    vols.to_csv(out / "vols.csv", index=False)

    # vendor prices from the model plus noise, with planted issues
    today = curves[curves.as_of_date == as_of]
    zc = bootstrap(today.tenor_years.tolist(), today.par_rate.tolist())
    sp_today = spreads[spreads.as_of_date == as_of].set_index("spread_index").spread_bp.to_dict()
    price_rows = []
    model_dirty = {}
    for r in instr.to_dict("records"):
        b = Bond.from_row(r)
        s = sp_today.get(b.spread_index, 0.0) if b.spread_index else 0.0
        dp = dirty_price(b, as_of, zc, s)
        model_dirty[b.instrument_id] = dp
        clean = dp - accrued_interest(b, as_of) + rng.normal(0, 0.03)
        pdate = as_of
        if b.instrument_id == "LWR-32":
            pdate = as_of - timedelta(days=6)        # stale: vendor has not updated in 4 business days
        if b.instrument_id == "CTI-36":
            clean += 0.85                             # vendor disagrees with model by ~0.85 points
        price_rows.append((b.instrument_id, as_of, "SAMPLE_VENDOR", round(clean, 4), pdate))
    pd.DataFrame(price_rows, columns=["instrument_id", "as_of_date", "source", "clean_price", "price_date"]).to_csv(
        out / "prices.csv", index=False)

    pos_rows, led_rows = [], []
    for book, holdings in POSITIONS.items():
        for iid, face in holdings.items():
            pos_rows.append((book, iid, as_of, face, "SAMPLE_BOOK_OF_RECORD"))
            led_face = face - 500_000 if iid == "UST-10Y" else face      # planted quantity break
            mv = led_face * model_dirty[iid] / 100 * (1 + rng.normal(0, 1e-6))
            led_rows.append((book, iid, as_of, led_face, round(mv, 2)))
    pd.DataFrame(pos_rows, columns=["book_id", "instrument_id", "as_of_date", "face_amount", "source_system"]).to_csv(
        out / "positions.csv", index=False)
    pd.DataFrame(led_rows, columns=["book_id", "instrument_id", "as_of_date", "face_amount", "market_value"]).to_csv(
        out / "ledger.csv", index=False)

    macro = pd.DataFrame([(sid, y, v[k]) for y, v in MACRO.items() for k, sid in enumerate(("UNRATE", "GDP_GROWTH"))],
                         columns=["series_id", "period", "value"])
    macro["source"] = "SAMPLE_MACRO"
    macro.to_csv(out / "macro.csv", index=False)

    generate_obligors(seed).to_csv(out / "obligors.csv", index=False)
    return out
