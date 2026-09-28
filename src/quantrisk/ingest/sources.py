"""Real-data extractors. They write the same landing-file format as the sample
generator, so switching from sample to live data needs no pipeline change.

FRED (Federal Reserve Bank of St. Louis) series used:
  DGS3MO DGS6MO DGS1 DGS2 DGS3 DGS5 DGS7 DGS10 DGS20 DGS30  Treasury constant-maturity yields
  UNRATE (unemployment), GDPC1 (real GDP)
Requires FRED_API_KEY in the environment (free key from fred.stlouisfed.org).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

from quantrisk.ingest import sample_data

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
TREASURY_SERIES = {0.25: "DGS3MO", 0.5: "DGS6MO", 1: "DGS1", 2: "DGS2", 3: "DGS3", 5: "DGS5", 7: "DGS7",
                   10: "DGS10", 20: "DGS20", 30: "DGS30"}


def _normalize_fred_series(series: pd.Series) -> pd.Series:
    """Return a unique, sorted and numeric FRED series keyed by observation date."""
    if series.empty:
        return series
    out = pd.to_numeric(series, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def fetch_fred(series_id: str, start: date, end: date, retries: int = 3) -> pd.Series:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY is not set")
    q = urllib.parse.urlencode({"series_id": series_id, "api_key": key, "file_type": "json",
                                "observation_start": start.isoformat(), "observation_end": end.isoformat()})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{FRED_URL}?{q}", timeout=30) as r:
                obs = json.load(r)["observations"]
            s = pd.Series({pd.Timestamp(o["date"]).date(): o["value"] for o in obs})
            return pd.to_numeric(s.replace(".", None), errors="coerce").dropna()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)                       # exponential backoff
    raise RuntimeError("unreachable")


def write_treasury_curves(out_dir: Path, start: date, end: date) -> Path:
    """Write curves.csv from FRED constant-maturity yields (percent -> decimal).

    Constant-maturity Treasury yields are par yields on a bond-equivalent basis,
    which is what the bootstrapper expects."""
    frames = {t: _normalize_fred_series(fetch_fred(sid, start, end)) for t, sid in TREASURY_SERIES.items()}
    wide = pd.concat(frames, axis=1).sort_index().dropna(how="any")
    rows = [(d.date(), float(t), float(v) / 100.0) for d, row in wide.iterrows() for t, v in row.items()]
    df = pd.DataFrame(rows, columns=["as_of_date", "tenor_years", "par_rate"])
    df["curve_id"] = "UST_PAR"
    df["source"] = "FRED_H15"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "curves.csv"
    df.to_csv(path, index=False)
    return path


def write_macro_series(out_dir: Path, start_year: int, end_year: int) -> Path:
    """Write a yearly macro.csv from FRED unemployment and real-GDP growth."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, int, float]] = []

    unemployment = _normalize_fred_series(fetch_fred("UNRATE", date(start_year, 1, 1), date(end_year, 12, 31)))
    year_unrate = unemployment.groupby(unemployment.index.year).mean().sort_index()
    for year, value in year_unrate.items():
        rows.append(("UNRATE", int(year), float(value)))

    gdp = _normalize_fred_series(fetch_fred("GDPC1", date(start_year, 1, 1), date(end_year, 12, 31)))
    annual_gdp = gdp.resample("YE").last().sort_index()
    growth = annual_gdp.pct_change().dropna() * 100.0
    for year_end in growth.index:
        rows.append(("GDP_GROWTH", int(pd.Timestamp(year_end).year), float(growth[year_end])))

    df = pd.DataFrame(rows, columns=["series_id", "period", "value"])
    df = (
        df.drop_duplicates(subset=["series_id", "period"], keep="last")
        .sort_values(["series_id", "period"])
        .reset_index(drop=True)
    )
    df["source"] = "FRED"
    path = out_dir / "macro.csv"
    df.to_csv(path, index=False)
    return path


def generate_live_landing(as_of: date, out_dir: str | Path, seed: int = 7,
                         history_start: date = date(2023, 1, 2)) -> Path:
    """Generate a raw landing folder using live FRED curves and macro inputs; keep the rest as sample data."""
    base_dir = Path(out_dir)
    live_dir = base_dir / as_of.isoformat()
    live_dir.mkdir(parents=True, exist_ok=True)

    sample_data.generate(as_of, base_dir, seed=seed, history_start=history_start)
    write_treasury_curves(live_dir, history_start, as_of)
    write_macro_series(live_dir, history_start.year, as_of.year)
    return live_dir
