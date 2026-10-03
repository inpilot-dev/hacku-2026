"""Settings from the environment, or from the repository's gitignored .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


def _env() -> dict[str, str]:
    values = {}
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                values[name.strip()] = value.split(" #")[0].strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if v})
    return values


@dataclass(frozen=True)
class Settings:
    wallet_url: str = "http://127.0.0.1:8000/api/v1"
    webhook_secret: str | None = None  # same value as the wallet's MANDATE_APPROVAL_WEBHOOK_SECRET
    tool_secret: str | None = None  # sent by every ElevenLabs tool as X-Tool-Secret
    caregiver_pin: str | None = None
    caregiver_phone: str | None = None
    elevenlabs_api_key: str | None = None
    agent_id: str | None = None
    phone_number_id: str | None = None

    @property
    def can_call(self) -> bool:
        return all((self.elevenlabs_api_key, self.agent_id, self.phone_number_id, self.caregiver_phone))

    @classmethod
    def from_env(cls) -> "Settings":
        e = _env()
        return cls(
            wallet_url=e.get("MANDATE_API_URL", cls.wallet_url),
            webhook_secret=e.get("MANDATE_APPROVAL_WEBHOOK_SECRET"),
            tool_secret=e.get("VOICE_TOOL_SECRET"),
            caregiver_pin=e.get("CAREGIVER_PIN"),
            caregiver_phone=e.get("CAREGIVER_PHONE"),
            elevenlabs_api_key=e.get("ELEVENLABS_API_KEY"),
            agent_id=e.get("ELEVENLABS_AGENT_ID"),
            phone_number_id=e.get("ELEVENLABS_PHONE_NUMBER_ID"),
        )
