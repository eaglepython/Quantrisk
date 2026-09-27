"""Configuration loading with environment-variable expansion and a stable hash.

Business settings (limits, scenarios, confidence levels) live in YAML so they can
change without a code release. The config hash is stored on every run for lineage.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), m.group(2) or "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def default_config_dir() -> Path:
    env = os.environ.get("QR_CONFIG_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), *here.parents]:
        cand = parent / "config" / "base.yaml"
        if cand.exists():
            return cand.parent
    raise FileNotFoundError("Could not find config/base.yaml; set QR_CONFIG_DIR")


@dataclass
class Settings:
    base: dict[str, Any]
    scenarios: dict[str, Any]
    limits: dict[str, Any]
    config_dir: Path
    hash: str = field(default="")

    # convenience accessors
    @property
    def database_url(self) -> str:
        return str(self.base["database"]["url"])

    @property
    def market(self) -> dict[str, Any]:
        return dict(self.base["market"])

    @property
    def risk(self) -> dict[str, Any]:
        return dict(self.base["risk"])

    @property
    def credit(self) -> dict[str, Any]:
        return dict(self.base["credit"])

    @property
    def dq(self) -> dict[str, Any]:
        return dict(self.base["data_quality"])

    @property
    def recon(self) -> dict[str, Any]:
        return dict(self.base["reconciliation"])

    def path(self, key: str) -> Path:
        return Path(self.base["paths"][key])


def load_settings(config_dir: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    cdir = Path(config_dir) if config_dir else default_config_dir()
    docs = {}
    for name in ("base", "scenarios", "limits"):
        with open(cdir / f"{name}.yaml", encoding="utf-8") as fh:
            docs[name] = _expand(yaml.safe_load(fh))
    if overrides:
        _deep_update(docs["base"], overrides)
    digest = hashlib.sha256(json.dumps(docs, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return Settings(base=docs["base"], scenarios=docs["scenarios"], limits=docs["limits"],
                    config_dir=cdir, hash=digest)


def _deep_update(target: dict[str, Any], upd: dict[str, Any]) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_update(target[k], v)
        else:
            target[k] = v
