from __future__ import annotations

import quantrisk.cli as cli


def test_daily_cli_uses_live_production_cycle(monkeypatch, tmp_path):
    calls = {}

    def fake_generate(as_of, out_dir, **kwargs):
        calls["generate"] = (as_of, out_dir)
        return out_dir / as_of.isoformat()

    monkeypatch.setattr("quantrisk.ingest.sources.generate_live_landing", fake_generate)
    monkeypatch.setattr("quantrisk.pipeline.run_daily", lambda *args, **kwargs: "R-OPS")
    monkeypatch.setattr("quantrisk.ops.check_data_freshness", lambda *args, **kwargs: (True, []))
    monkeypatch.setattr("quantrisk.ops.build_alert_summary", lambda *args, **kwargs: {"status": "OK", "breaches": []})
    monkeypatch.setattr("quantrisk.ops.generate_daily_summary", lambda *args, **kwargs: "Operational summary")
    monkeypatch.setattr(cli, "load_settings", lambda *args, **kwargs: type("S", (), {
        "path": lambda self, key: tmp_path / key,
        "database_url": "sqlite:///:memory:",
        "hash": "abc123",
    })())
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)

    rc = cli.main(["daily", "--as-of", "2026-09-25", "--no-report", "--no-ai"])

    assert rc == 0
    assert calls["generate"][0].isoformat() == "2026-09-25"


def test_health_cli_reports_stale_data(monkeypatch, tmp_path):
    monkeypatch.setattr("quantrisk.ops.check_data_freshness", lambda *args, **kwargs: (False, ["curve.csv", "prices.csv"]))
    monkeypatch.setattr(cli, "load_settings", lambda *args, **kwargs: type("S", (), {
        "path": lambda self, key: tmp_path / key,
        "database_url": "sqlite:///:memory:",
        "hash": "abc123",
    })())
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)

    rc = cli.main(["health"])

    assert rc == 1
