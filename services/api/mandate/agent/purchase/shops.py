"""What checkout attempts learned about shops, so later purchases skip them instead of trying again.

A shop that needed an account or blocked the browser is remembered for a week,
one whose guest checkout stalled for a day, in services/api/.data/shops.json
(gitignored). Remembered shops are still
offered to the shopper as links; they are only not checked out by the agent.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / ".data" / "shops.json"
# A login wall or bot wall is lasting; a checkout that just stalled may work another day.
TTL_S = {"account_required": 7 * 24 * 3600, "blocked": 7 * 24 * 3600, "failed": 24 * 3600}
REMEMBERED = tuple(TTL_S)


class ShopMemory:
    def __init__(self, path: str | Path | None = None, now=time.time):
        self.path = Path(path or os.environ.get("MANDATE_SHOP_MEMORY") or DEFAULT_PATH)
        self.now = now
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, host: str) -> dict | None:
        """{checkout, reason} for a shop remembered within the last week, else None."""
        with self._lock:
            entry = self._load().get(host)
        if entry and entry["checkout"] in REMEMBERED and self.now() - entry["at"] < TTL_S[entry["checkout"]]:
            return {"checkout": entry["checkout"], "reason": entry["reason"]}
        return None

    def remember(self, host: str, checkout: str, reason: str) -> None:
        if checkout not in REMEMBERED:
            return
        with self._lock:
            data = self._load()
            data[host] = {"checkout": checkout, "reason": reason, "at": self.now()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, self.path)
