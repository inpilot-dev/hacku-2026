"""The user's sign-ins at each store (GET/POST/DELETE /stores...).

A connection is the shop's own session (cookies and localStorage) after the
user signed in themselves in the shop's page. It is kept server-side only, in
one 0600 file per user and store under MANDATE_STORE_DATA_DIR (default
services/api/.data/stores, gitignored), and is never returned by the API or
shown to a model. It holds personal data such as the account's address, so
nothing here logs its contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from mandate.payments.auth import Actor
from mandate.payments.clock import iso
from mandate.payments.errors import not_found

from .registry import STORES, SuperwebStore
from .steel import StoreBrowserError

DEFAULT_DIR = Path(__file__).resolve().parents[2] / ".data" / "stores"
LOGIN_TIMEOUT_S = 10 * 60


def _store(store_id: str) -> SuperwebStore:
    store = STORES.get(store_id)
    if store is None:
        raise not_found("Store")
    return store


class StoreConnections:
    def __init__(self, browser, now: Callable[[], datetime], data_dir: str | Path | None = None,
                 monotonic: Callable[[], float] = time.monotonic):
        self.browser = browser
        self.now = now
        self.monotonic = monotonic
        self.data_dir = Path(data_dir or os.environ.get("MANDATE_STORE_DATA_DIR") or DEFAULT_DIR)
        self._pending: dict[tuple[str, str], tuple[object, float]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- storage

    def _path(self, user_id: str, store_id: str) -> Path:
        safe = user_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id) else hashlib.sha256(user_id.encode()).hexdigest()
        return self.data_dir / safe / f"{store_id}.json"

    def _read(self, user_id: str, store_id: str) -> dict | None:
        path = self._path(user_id, store_id)
        return json.loads(path.read_text()) if path.is_file() else None

    def _write(self, user_id: str, store_id: str, record: dict) -> None:
        path = self._path(user_id, store_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, path)

    def session_state(self, user_id: str, store_id: str) -> dict | None:
        """The saved shop session for cart calls, or None when not connected or expired."""
        record = self._read(user_id, store_id)
        if record is None or record.get("expired_at"):
            return None
        return {"cookies": record["cookies"], "local_storage": record["local_storage"]}

    def mark_expired(self, user_id: str, store_id: str) -> None:
        record = self._read(user_id, store_id)
        if record is not None and not record.get("expired_at"):
            record["expired_at"] = iso(self.now())
            self._write(user_id, store_id, record)

    # ----------------------------------------------------------------- public

    def list(self, actor: Actor) -> dict:
        return {"stores": [self.get(actor, store_id) for store_id in STORES]}

    def get(self, actor: Actor, store_id: str) -> dict:
        store = _store(store_id)
        key = (actor.family_id, store_id)
        with self._lock:
            pending = self._pending.get(key)
        if pending is not None:
            window, started = pending
            try:
                state = self.browser.poll_login(window, store)
            except StoreBrowserError as exc:
                return self._view(store, "awaiting_login", message=f"Waiting for sign-in ({exc}).")
            if state is not None:
                self._write(actor.family_id, store_id, {**state, "connected_at": iso(self.now()),
                                                       "expired_at": None})
                self._finish(key)
            elif self.monotonic() - started > LOGIN_TIMEOUT_S:
                self._finish(key)
                return self._view(store, "not_connected", message="Sign-in timed out. Start again when ready.")
            else:
                return self._view(store, "awaiting_login", viewer_url=self.browser.viewer_url,
                                  message=f"Sign in to {store.name} in the store window: your mobile number, "
                                          "then the SMS code.")
        record = self._read(actor.family_id, store_id)
        if record is None:
            return self._view(store, "not_connected", message=f"Not connected. Sign in to let Kumi fill your "
                                                              f"{store.name} cart.")
        if record.get("expired_at"):
            return self._view(store, "expired", connected_at=record["connected_at"],
                              message=f"{store.name} signed you out. Sign in again to keep filling this cart.")
        return self._view(store, "connected", connected_at=record["connected_at"],
                          message=f"Connected. Kumi can add items to your {store.name} cart but cannot check out.")

    def start(self, actor: Actor, store_id: str) -> dict:
        store = _store(store_id)
        key = (actor.family_id, store_id)
        self._finish(key)  # a second Connect replaces an unfinished sign-in
        window = self.browser.begin_login(store)
        with self._lock:
            self._pending[key] = (window, self.monotonic())
        return self.get(actor, store_id)

    def disconnect(self, actor: Actor, store_id: str) -> dict:
        _store(store_id)
        self._finish((actor.family_id, store_id))
        self._path(actor.family_id, store_id).unlink(missing_ok=True)
        return self.get(actor, store_id)

    # ---------------------------------------------------------------- helpers

    def _finish(self, key: tuple[str, str]) -> None:
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending is not None:
            self.browser.end_login(pending[0])

    @staticmethod
    def _view(store: SuperwebStore, status: str, *, connected_at: str | None = None,
              viewer_url: str | None = None, message: str) -> dict:
        return {"store_id": store.id, "name": store.name, "status": status, "connected_at": connected_at,
                "viewer_url": viewer_url, "message": message}
