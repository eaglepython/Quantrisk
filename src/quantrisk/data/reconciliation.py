"""Reconciliation of the risk system against the accounting ledger.

Two levels:
  * position quantity (face) per book and instrument, tolerance in face units
  * total market value per book, tolerance as a percentage of ledger value
Every comparison is logged, matches included, so the log is complete evidence.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def reconcile(run_id: str, as_of: date, positions: pd.DataFrame, valuation: pd.DataFrame, ledger: pd.DataFrame,
              mv_tol_pct: float, qty_tol: float) -> pd.DataFrame:
    rows = []
    p = positions.set_index(["book_id", "instrument_id"]).face_amount
    lq = ledger.set_index(["book_id", "instrument_id"]).face_amount
    for key in sorted(set(p.index) | set(lq.index)):
        a, b = p.get(key), lq.get(key)
        diff = None if a is None or b is None else float(a - b)
        ok = diff is not None and abs(diff) <= qty_tol
        rows.append({
            "recon_id": f"{run_id}:QTY:{key[0]}:{key[1]}", "run_id": run_id, "as_of_date": as_of,
            "book_id": key[0], "instrument_id": key[1], "measure": "face_amount",
            "value_risk": None if a is None else float(a), "value_ledger": None if b is None else float(b),
            "difference": diff, "tolerance": float(qty_tol), "status": "MATCH" if ok else "BREAK",
            "owner": None if ok else "middle-office", "comment": None if ok else _qty_comment(a, b),
            "resolved_at": None,
        })
    mv_risk = valuation.groupby("book_id").market_value.sum()
    mv_led = ledger.groupby("book_id").market_value.sum()
    for book in sorted(set(mv_risk.index) | set(mv_led.index)):
        a, b = mv_risk.get(book), mv_led.get(book)
        diff = None if a is None or b is None else float(a - b)
        tol = float(abs(b) * mv_tol_pct) if b is not None else 0.0
        ok = diff is not None and abs(diff) <= tol
        rows.append({
            "recon_id": f"{run_id}:MV:{book}", "run_id": run_id, "as_of_date": as_of, "book_id": book,
            "instrument_id": None, "measure": "market_value", "value_risk": None if a is None else float(a),
            "value_ledger": None if b is None else float(b), "difference": diff, "tolerance": tol,
            "status": "MATCH" if ok else "BREAK", "owner": None if ok else "product-control",
            "comment": None if ok else "Book market value outside tolerance; check quantity breaks first",
            "resolved_at": None,
        })
    return pd.DataFrame(rows)


def _qty_comment(a: float | None, b: float | None) -> str:
    if a is None:
        return "Position in ledger but missing from risk system"
    if b is None:
        return "Position in risk system but missing from ledger"
    return "Face amount differs between book of record and ledger"
