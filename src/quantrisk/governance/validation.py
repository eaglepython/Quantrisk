"""Automated model-validation tests run inside every daily batch.

Results land in model_validation so model-risk reviewers can see ongoing
monitoring evidence without re-running anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Check:
    model_id: str
    test_name: str
    value: float | None
    threshold: str
    status: str
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "test_name": self.test_name, "value": self.value,
                "threshold": self.threshold, "status": self.status, "detail": self.detail}


def band(value: float, pass_if: Any, watch_if: Any) -> str:
    if pass_if(value):
        return "PASS"
    if watch_if(value):
        return "WATCH"
    return "FAIL"


def load_inventory(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / "model_inventory.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(yaml.safe_load(fh)["models"])
