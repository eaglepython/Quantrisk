"""Load a raw landing folder into PostgreSQL staging tables.

Steps: read CSV -> coerce types -> uniqueness and business DQ rules -> idempotent
write stamped with load_id. A CRITICAL failure raises DataQualityError before any
model runs, so the report never shows numbers built on broken inputs.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from quantrisk.config import Settings
from quantrisk.data import db, schema
from quantrisk.ingest.dq_rules import CRITICAL, DataQualityError, DQResult, coerce, duplicate_keys, run_rules
from quantrisk.logging_setup import get_logger

log = get_logger(__name__)

FILES = ["instruments", "curves", "spreads", "vols", "prices", "positions", "ledger", "macro", "obligors"]


def read_landing(folder: Path) -> tuple[dict[str, pd.DataFrame], list[DQResult]]:
    frames, results = {}, []
    for name in FILES:
        path = folder / f"{name}.csv"
        if not path.exists():
            results.append(DQResult(f"file_present_{name}", name, CRITICAL, 0, 1, f"{path} not found"))
            continue
        raw = pd.read_csv(path)
        typed, r = coerce(name, raw)
        results.append(r)
        results.append(duplicate_keys(name, typed))
        frames[name] = typed
    return frames, results


def load(engine: Engine, settings: Settings, as_of: date, raw_root: Path | None = None) -> tuple[str, list[DQResult]]:
    folder = (raw_root or settings.path("raw")) / as_of.isoformat()
    load_id = f"L-{as_of:%Y%m%d}-{uuid.uuid4().hex[:8]}"
    frames, results = read_landing(folder)
    if all(n in frames for n in FILES):
        results += run_rules(frames, as_of, settings.dq, settings.market["curve_tenors"], settings.market["spread_indices"])
    critical = [r for r in results if r.severity == CRITICAL and r.status == "FAIL"]
    if critical:
        log.error("critical data-quality failures", extra={"ctx": {"failures": [r.check_name for r in critical]}})
        raise DataQualityError(critical)

    def stamp(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        out = df[cols].copy()
        out["load_id"] = load_id
        return out

    f = frames
    instr = f["instruments"].copy()
    instr["spread_index"] = instr["spread_index"].where(instr["spread_index"].notna(), None)
    instr_cols = [c.name for c in schema.ref_instrument.columns]
    db.upsert_rows(engine, schema.ref_instrument, instr[instr_cols])

    dates = sorted(f["curves"].as_of_date.unique())
    db.replace_rows(engine, schema.mkt_curve_point,
                    stamp(f["curves"], ["curve_id", "as_of_date", "tenor_years", "par_rate", "source"]),
                    where_in={"as_of_date": dates})
    db.replace_rows(engine, schema.mkt_spread,
                    stamp(f["spreads"], ["spread_index", "as_of_date", "spread_bp", "source"]),
                    where_in={"as_of_date": sorted(f["spreads"].as_of_date.unique())})
    db.replace_rows(engine, schema.mkt_vol,
                    stamp(f["vols"], ["vol_index", "as_of_date", "normal_vol_bp", "source"]),
                    where_in={"as_of_date": sorted(f["vols"].as_of_date.unique())})
    db.replace_rows(engine, schema.mkt_price,
                    stamp(f["prices"], ["instrument_id", "as_of_date", "source", "clean_price", "price_date"]),
                    where={"as_of_date": as_of})
    db.replace_rows(engine, schema.pos_position,
                    stamp(f["positions"], ["book_id", "instrument_id", "as_of_date", "face_amount", "source_system"]),
                    where={"as_of_date": as_of})
    db.replace_rows(engine, schema.ledger_balance,
                    stamp(f["ledger"], ["book_id", "instrument_id", "as_of_date", "face_amount", "market_value"]),
                    where={"as_of_date": as_of})
    macro = f["macro"].copy()
    macro["period"] = macro["period"].astype(int)
    db.replace_rows(engine, schema.macro_series, stamp(macro, ["series_id", "period", "value", "source"]),
                    where_in={"series_id": macro.series_id.unique()})
    ob = f["obligors"].copy()
    ob["year"] = ob["year"].astype(int)
    ob["default_flag"] = ob["default_flag"].astype("Int64").astype(object).where(ob["default_flag"].notna(), None)
    for c in ("ead", "seniority"):
        ob[c] = ob[c].astype(object).where(ob[c].notna(), None)
    obl_cols = [c.name for c in schema.credit_obligor.columns if c.name != "load_id"]
    db.replace_rows(engine, schema.credit_obligor, stamp(ob, obl_cols), where_in={"year": sorted(ob.year.unique())})
    log.info("landing loaded", extra={"ctx": {"load_id": load_id, "as_of": str(as_of),
                                               "curve_days": len(dates), "positions": len(f["positions"])}})
    return load_id, results
