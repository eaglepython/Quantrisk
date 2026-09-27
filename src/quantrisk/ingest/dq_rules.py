"""Data-quality rules. CRITICAL failures hold the run; WARNINGs are reported.

Each rule returns a DQResult so the outcome of every check is stored in
dq_check_result, not just the failures. Auditors want to see that the check ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from quantrisk.fixed_income.curves import bootstrap

CRITICAL = "CRITICAL"
WARNING = "WARNING"

SCHEMAS: dict[str, dict[str, str]] = {
    "instruments": {"instrument_id": "str", "asset_class": "str", "coupon": "float", "issue_date": "date",
                    "maturity_date": "date", "frequency": "int", "day_count": "str"},
    "curves": {"curve_id": "str", "as_of_date": "date", "tenor_years": "float", "par_rate": "float"},
    "spreads": {"spread_index": "str", "as_of_date": "date", "spread_bp": "float"},
    "vols": {"vol_index": "str", "as_of_date": "date", "normal_vol_bp": "float"},
    "prices": {"instrument_id": "str", "as_of_date": "date", "clean_price": "float", "price_date": "date"},
    "positions": {"book_id": "str", "instrument_id": "str", "as_of_date": "date", "face_amount": "float"},
    "ledger": {"book_id": "str", "instrument_id": "str", "as_of_date": "date", "face_amount": "float",
               "market_value": "float"},
    "macro": {"series_id": "str", "period": "int", "value": "float"},
    "obligors": {"obligor_id": "str", "year": "int", "leverage": "float", "interest_coverage": "float",
                 "roa": "float", "current_ratio": "float", "log_assets": "float"},
}

KEYS: dict[str, list[str]] = {
    "instruments": ["instrument_id"], "curves": ["curve_id", "as_of_date", "tenor_years"],
    "spreads": ["spread_index", "as_of_date"], "vols": ["vol_index", "as_of_date"],
    "prices": ["instrument_id", "as_of_date", "source"], "positions": ["book_id", "instrument_id", "as_of_date"],
    "ledger": ["book_id", "instrument_id", "as_of_date"], "macro": ["series_id", "period"],
    "obligors": ["obligor_id", "year"],
}


@dataclass
class DQResult:
    check_name: str
    table_name: str
    severity: str
    rows_checked: int
    rows_failed: int
    detail: str = ""
    status: str = field(init=False)

    def __post_init__(self) -> None:
        self.status = "PASS" if self.rows_failed == 0 else "FAIL"

    def as_row(self) -> dict[str, Any]:
        return {"check_name": self.check_name, "table_name": self.table_name, "severity": self.severity,
                "rows_checked": self.rows_checked, "rows_failed": self.rows_failed, "status": self.status,
                "detail": self.detail[:2000]}


class DataQualityError(RuntimeError):
    def __init__(self, failures: list[DQResult]):
        self.failures = failures
        super().__init__("; ".join(f"{f.check_name}: {f.detail}" for f in failures))


def coerce(name: str, df: pd.DataFrame) -> tuple[pd.DataFrame, DQResult]:
    """Type the frame per its schema; count rows that are missing required values or fail to parse."""
    schema = SCHEMAS[name]
    missing_cols = [c for c in schema if c not in df.columns]
    if missing_cols:
        return df, DQResult(f"schema_{name}", name, CRITICAL, len(df), len(df), f"missing columns {missing_cols}")
    bad = pd.Series(False, index=df.index)
    out = df.copy()
    for col, typ in schema.items():
        if typ == "float":
            out[col] = pd.to_numeric(out[col], errors="coerce")
        elif typ == "int":
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
        elif typ == "date":
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
        bad |= out[col].isna()
    return out, DQResult(f"schema_{name}", name, CRITICAL, len(df), int(bad.sum()),
                         "" if not bad.any() else f"{int(bad.sum())} rows with null/unparseable required fields")


def duplicate_keys(name: str, df: pd.DataFrame) -> DQResult:
    d = int(df.duplicated(subset=KEYS[name]).sum())
    return DQResult(f"unique_{name}", name, CRITICAL, len(df), d, f"{d} duplicate keys" if d else "")


def run_rules(frames: dict[str, pd.DataFrame], as_of: date, cfg: dict[str, Any], tenors: list[float],
              spread_indices: list[str]) -> list[DQResult]:
    res: list[DQResult] = []
    curves = frames["curves"]
    today = curves[curves.as_of_date == as_of]

    missing = sorted(set(tenors) - set(today.tenor_years.astype(float)))
    res.append(DQResult("curve_tenors_complete", "mkt_curve_point", CRITICAL, len(tenors), len(missing),
                        f"missing tenors {missing} on {as_of}" if missing else ""))

    lo, hi = cfg["rate_bounds"]
    oob = curves[(curves.par_rate < lo) | (curves.par_rate > hi)]
    res.append(DQResult("curve_rate_bounds", "mkt_curve_point", CRITICAL, len(curves), len(oob),
                        f"{len(oob)} rates outside [{lo}, {hi}]" if len(oob) else ""))

    dates = sorted(curves.as_of_date.unique())
    if as_of in dates and dates.index(as_of) > 0:
        prev = curves[curves.as_of_date == dates[dates.index(as_of) - 1]].set_index("tenor_years").par_rate
        move = (today.set_index("tenor_years").par_rate - prev).abs() * 1e4
        big = move[move > cfg["max_curve_move_bp"]]
        res.append(DQResult("curve_day_over_day_move", "mkt_curve_point", WARNING, len(move), len(big),
                            f"moves above {cfg['max_curve_move_bp']}bp at tenors {', '.join(f'{t:g}Y' for t in big.index)}" if len(big) else ""))

    if len(today) == len(tenors):
        zc = bootstrap(today.tenor_years.tolist(), today.par_rate.tolist())
        fwd = [zc.forward(a, b) for a, b in zip(zc.tenors[:-1], zc.tenors[1:], strict=True)]
        neg = int(np.sum(np.array(fwd) < 0))
        res.append(DQResult("curve_negative_forwards", "mkt_curve_point", WARNING, len(fwd), neg,
                            f"{neg} negative forward rates" if neg else ""))

    sp = frames["spreads"]
    miss_sp = sorted(set(spread_indices) - set(sp[sp.as_of_date == as_of].spread_index))
    res.append(DQResult("spreads_present", "mkt_spread", CRITICAL, len(spread_indices), len(miss_sp),
                        f"missing spread indices {miss_sp}" if miss_sp else ""))

    vols = frames["vols"]
    res.append(DQResult("vol_present", "mkt_vol", CRITICAL, 1, int((vols.as_of_date == as_of).sum() == 0),
                        "" if (vols.as_of_date == as_of).any() else "no vol for as-of date"))

    pos, instr = frames["positions"], frames["instruments"]
    unmapped = pos[~pos.instrument_id.isin(instr.instrument_id)]
    res.append(DQResult("positions_mapped", "pos_position", CRITICAL, len(pos), len(unmapped),
                        f"unknown instruments {unmapped.instrument_id.tolist()}" if len(unmapped) else ""))

    matured = instr[instr.maturity_date <= as_of]
    res.append(DQResult("instrument_not_matured", "ref_instrument", WARNING, len(instr), len(matured),
                        f"matured: {matured.instrument_id.tolist()}" if len(matured) else ""))

    prices = frames["prices"]
    held = set(pos.instrument_id)
    no_price = sorted(held - set(prices.instrument_id))
    res.append(DQResult("price_available", "mkt_price", WARNING, len(held), len(no_price),
                        f"no vendor price for {no_price}" if no_price else ""))
    bdays = prices.apply(lambda r: int(np.busday_count(r.price_date, r.as_of_date)), axis=1) if len(prices) else pd.Series(dtype=int)
    stale = prices[bdays > cfg["stale_price_days"]]
    res.append(DQResult("price_not_stale", "mkt_price", WARNING, len(prices), len(stale),
                        f"stale (> {cfg['stale_price_days']} business days): {', '.join(stale.instrument_id)}" if len(stale) else ""))

    macro = frames["macro"]
    years = macro.groupby("series_id").period.nunique()
    res.append(DQResult("macro_series_complete", "macro_series", WARNING, 2,
                        int(sum(1 for s in ("UNRATE", "GDP_GROWTH") if s not in years.index)), ""))

    ob = frames["obligors"]
    bad_ob = ob[(ob.leverage < 0) | (ob.leverage > 1.5) | (ob.interest_coverage <= 0) | (ob.current_ratio <= 0)]
    res.append(DQResult("obligor_ranges", "credit_obligor", WARNING, len(ob), len(bad_ob),
                        f"{len(bad_ob)} obligor rows outside plausible ranges" if len(bad_ob) else ""))
    return res


def model_vs_vendor(valuation: pd.DataFrame, tol: float) -> DQResult:
    v = valuation.dropna(subset=["vendor_price"]).drop_duplicates("instrument_id")
    diff = (v.clean_price - v.vendor_price).abs()
    bad = v[diff > tol]
    detail = ", ".join(f"{r.instrument_id} model {r.clean_price:.3f} vs vendor {r.vendor_price:.3f}" for r in bad.itertuples())
    return DQResult("model_vs_vendor_price", "position_valuation", WARNING, len(v), len(bad), detail)
