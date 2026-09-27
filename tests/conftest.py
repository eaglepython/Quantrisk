from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from quantrisk.config import load_settings
from quantrisk.fixed_income.curves import bootstrap

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 25)
TENORS = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
PAR = [0.043, 0.042, 0.0405, 0.0385, 0.038, 0.0385, 0.04, 0.042, 0.0465, 0.0475]


@pytest.fixture(scope="session")
def curve():
    return bootstrap(TENORS, PAR)


@pytest.fixture(scope="session")
def pipeline_run(tmp_path_factory):
    """Generate sample data and run the full daily batch once per test session.

    Uses QR_TEST_DATABASE_URL when set (CI points it at PostgreSQL), else SQLite."""
    tmp = tmp_path_factory.mktemp("qr")
    url = os.environ.get("QR_TEST_DATABASE_URL", f"sqlite:///{tmp / 'test.db'}")
    settings = load_settings(ROOT / "config", overrides={
        "database": {"url": url},
        "paths": {"raw": str(tmp / "raw"), "curated": str(tmp / "curated"), "reports": str(tmp / "reports")},
        "risk": {"mc_paths": 4000},
    })
    from quantrisk.data import db
    from quantrisk.ingest.sample_data import generate
    from quantrisk.pipeline import run_daily

    generate(AS_OF, settings.path("raw"))
    run_id = run_daily(AS_OF, settings)
    return settings, db.make_engine(url), run_id
