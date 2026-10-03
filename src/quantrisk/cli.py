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
import time
from datetime import date, timedelta
from pathlib import Path

from quantrisk.config import load_profile_settings, load_settings
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
    ap.add_argument("--profile", choices=("demo", "portfolio"), default="demo",
                    help="isolate demo results or approved portfolio-feed results")
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
    p.add_argument("--with-real-data", action="store_true",
                   help="use FRED curves/macros; portfolio profile also requires real portfolio CSVs")
    p.add_argument("--portfolio-input-dir", type=Path,
                   help="folder with real instruments, spreads, vols, prices, positions, ledger, and obligors CSVs")
    p.add_argument("--no-report", action="store_true")
    p = sub.add_parser("live")
    p.add_argument("--as-of", type=_date, default=_date("today"))
    p.add_argument("--every-hours", type=float, default=24.0,
                   help="sleep between each live refresh; use 24 for daily batches")
    p.add_argument("--no-report", action="store_true")
    p = sub.add_parser("daily")
    p.add_argument("--as-of", type=_date, required=True)
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p = sub.add_parser("health")
    p.add_argument("--as-of", type=_date, default=_date("today"))
    p = sub.add_parser("ai")
    p.add_argument("--prompt", default="Summarize the current risk posture and key exposures.")
    p = sub.add_parser("report")
    p.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)

    setup_logging(json_output=not args.plain_logs)
    settings = (load_settings(args.config_dir) if args.profile == "demo"
                else load_profile_settings(args.profile, args.config_dir))

    if args.profile == "portfolio" and args.cmd in {"generate-sample", "live", "daily"}:
        raise SystemExit(
            f"{args.cmd} is disabled for the portfolio profile until it accepts an explicit approved input feed. "
            "Use `run --with-real-data --portfolio-input-dir <folder>` so sample holdings cannot enter portfolio results."
        )

    if args.cmd == "init-db":
        if args.ddl:
            print(db.postgres_ddl())
        else:
            db.init_db(db.make_engine(settings.database_url))
            print("tables created")
        return 0
    if args.cmd == "generate-sample":
        if args.profile != "demo":
            raise SystemExit("sample generation is restricted to the demo profile")
        from quantrisk.ingest.sample_data import generate
        out = generate(args.as_of, settings.path("raw"), seed=args.seed)
        print(f"sample landing written to {out}")
        return 0
    if args.cmd == "run":
        if args.with_sample and args.with_real_data:
            raise SystemExit("choose only one of --with-sample or --with-real-data")
        if args.profile == "portfolio" and args.with_sample:
            raise SystemExit("sample inputs cannot be written into the isolated portfolio profile")
        if args.profile == "portfolio" and args.with_real_data and not args.portfolio_input_dir:
            raise SystemExit("portfolio profile requires --portfolio-input-dir; it will not substitute sample holdings or prices")
        if args.profile == "demo" and args.portfolio_input_dir:
            raise SystemExit("--portfolio-input-dir requires --profile portfolio")
        if args.with_sample:
            from quantrisk.ingest.sample_data import generate
            generate(args.as_of, settings.path("raw"))
        elif args.with_real_data:
            from quantrisk.ingest.sources import generate_live_landing
            generate_live_landing(
                args.as_of, settings.path("raw"), portfolio_input_dir=args.portfolio_input_dir,
            )
        from quantrisk.pipeline import run_daily
        try:
            run_id = run_daily(args.as_of, settings, make_report=not args.no_report)
        except DataQualityError:
            return 2          # distinct exit code: run held on data quality
        _publish_to_s3(settings, run_id)
        print(run_id)
        return 0
    if args.cmd == "live":
        from quantrisk.ingest.sources import generate_live_landing
        from quantrisk.pipeline import run_daily

        while True:
            as_of = _date("today") if args.as_of is None else args.as_of
            try:
                generate_live_landing(as_of, settings.path("raw"))
                run_id = run_daily(as_of, settings, make_report=not args.no_report)
                print(run_id)
            except DataQualityError:
                print(f"live run held for {as_of.isoformat()}")
            except Exception as exc:  # pragma: no cover - long-running process
                print(f"live run failed: {exc}")
            if args.every_hours <= 0:
                return 0
            time.sleep(args.every_hours * 3600)
    if args.cmd == "daily":
        from quantrisk.daily import run_operational_daily
        try:
            payload = run_operational_daily(args.as_of, settings, with_real_data=True, no_report=args.no_report,
                                          no_ai=args.no_ai)
        except RuntimeError as exc:
            print(str(exc))
            return 1
        print(payload)
        return 0
    if args.cmd == "health":
        from quantrisk.ops import check_data_freshness
        fresh, stale = check_data_freshness(settings.path("raw"), args.as_of)
        if not fresh:
            print(f"data freshness failed: {stale}")
            return 1
        print(f"data freshness OK for {args.as_of.isoformat()}")
        return 0
    if args.cmd == "ai":
        from quantrisk.ai import generate_summary
        print(generate_summary(args.prompt))
        return 0
    if args.cmd == "report":
        from quantrisk.reporting.report import build_report
        path = build_report(db.make_engine(settings.database_url), args.run_id, settings)
        print(Path(path))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
