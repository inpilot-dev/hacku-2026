"""Secrets for the agent's model providers: the environment first, then the repository .env."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ENV = Path(__file__).resolve().parents[4] / ".env"


def env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() == name:
                return raw.strip().strip('"').strip("'") or None
    return None
