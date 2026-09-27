"""Real-data extractors. They write the same landing-file format as the sample
generator, so switching from sample to live data needs no pipeline change.

FRED (Federal Reserve Bank of St. Louis) series used:
  DGS3MO DGS6MO DGS1 DGS2 DGS3 DGS5 DGS7 DGS10 DGS20 DGS30  Treasury constant-maturity yields
  UNRATE (unemployment), A191RL1Q225SBEA (real GDP growth)
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

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
TREASURY_SERIES = {0.25: "DGS3MO", 0.5: "DGS6MO", 1: "DGS1", 2: "DGS2", 3: "DGS3", 5: "DGS5", 7: "DGS7",
                   10: "DGS10", 20: "DGS20", 30: "DGS30"}


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
    frames = {t: fetch_fred(sid, start, end) for t, sid in TREASURY_SERIES.items()}
    wide = pd.DataFrame(frames).dropna()
    rows = [(d, t, v / 100.0) for d, r in wide.iterrows() for t, v in r.items()]
    df = pd.DataFrame(rows, columns=["as_of_date", "tenor_years", "par_rate"])
    df["curve_id"] = "UST_PAR"
    df["source"] = "FRED_H15"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "curves.csv"
    df.to_csv(path, index=False)
    return path
