"""Command-line entry point.

  quantrisk init-db                      create tables (and print Postgres DDL with --ddl)
  quantrisk generate-sample --as-of D    write a raw landing folder of sample data
  quantrisk run --as-of D                run the full daily batch
  quantrisk report --run-id R            rebuild the HTML report for a past run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from quantrisk.config import load_settings
from quantrisk.data import db
from quantrisk.ingest.dq_rules import DataQualityError
from quantrisk.logging_setup import setup_logging


def _date(s: str) -> date:
    """ISO date, or 'today' = the latest business day on or before today (for the scheduler)."""
    if s.lower() == "today":
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    return date.fromisoformat(s)


def _publish_to_s3(settings, run_id: str) -> None:
    """Copy the report to S3 when QR_S3_BUCKET is set (cloud deployment). No-op locally."""
    bucket = os.environ.get("QR_S3_BUCKET")
    if not bucket:
        return
    import boto3  # installed in the cloud image only

    s3 = boto3.client("s3")
    for path in settings.path("reports").rglob(f"*{run_id}*.html"):
        s3.upload_file(str(path), bucket, f"reports/{path.parent.name}/{path.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quantrisk")
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--plain-logs", action="store_true", help="human-readable logs instead of JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-db")
    p.add_argument("--ddl", action="store_true", help="print PostgreSQL DDL instead of creating tables")
    p = sub.add_parser("generate-sample")
    p.add_argument("--as-of", type=_date, required=True)
    p.add_argument("--seed", type=int, default=7)
    p = sub.add_parser("run")
    p.add_argument("--as-of", type=_date, required=True)
    p.add_argument("--with-sample", action="store_true", help="generate sample landing files first")
    p.add_argument("--no-report", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)

    setup_logging(json_output=not args.plain_logs)
    settings = load_settings(args.config_dir)

    if args.cmd == "init-db":
        if args.ddl:
            print(db.postgres_ddl())
        else:
            db.init_db(db.make_engine(settings.database_url))
            print("tables created")
        return 0
    if args.cmd == "generate-sample":
        from quantrisk.ingest.sample_data import generate
        out = generate(args.as_of, settings.path("raw"), seed=args.seed)
        print(f"sample landing written to {out}")
        return 0
    if args.cmd == "run":
        if args.with_sample:
            from quantrisk.ingest.sample_data import generate
            generate(args.as_of, settings.path("raw"))
        from quantrisk.pipeline import run_daily
        try:
            run_id = run_daily(args.as_of, settings, make_report=not args.no_report)
        except DataQualityError:
            return 2          # distinct exit code: run held on data quality
        _publish_to_s3(settings, run_id)
        print(run_id)
        return 0
    if args.cmd == "report":
        from quantrisk.reporting.report import build_report
        path = build_report(db.make_engine(settings.database_url), args.run_id, settings)
        print(Path(path))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
