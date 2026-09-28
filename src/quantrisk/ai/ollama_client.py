from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

DEFAULT_MODEL = os.environ.get("QUANTRISK_OLLAMA_MODEL", "llama3.2")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def is_ollama_available() -> bool:
    """Check whether the local Ollama server is reachable."""
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_model(model_name: str = DEFAULT_MODEL) -> str:
    """Ask Ollama to pull a free local model if it is not present."""
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.load(resp)
        available = {m.get("name") for m in payload.get("models", [])}
        if model_name in available:
            return model_name
    except Exception:
        pass

    try:
        body = json.dumps({"name": model_name}).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/pull",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"pull failed with status {resp.status}")
    except Exception:
        raise
    return model_name


def generate_summary(prompt: str, *, model: str = DEFAULT_MODEL, temperature: float = 0.2) -> str:
    """Generate a short local LLM summary without sending data to a remote service."""
    if not is_ollama_available():
        return "Ollama is not running locally. Start it with: ollama serve"

    model_name = ensure_model(model)
    body = json.dumps({
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
        return data.get("response", "").strip()
