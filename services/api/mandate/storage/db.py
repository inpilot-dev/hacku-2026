"""SQLite connections and the wallet's single write-transaction boundary.

Every financial state change runs inside ``write_tx``: ``BEGIN IMMEDIATE``
takes SQLite's write lock up front, so check-budget-then-reserve can never
interleave with another writer. Lock contention surfaces as ``DatabaseBusy``
(HTTP 503, retryable) and never as an approval.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 3
BUSY_TIMEOUT_MS = 5000


class DatabaseBusy(Exception):
    """The write lock could not be taken within the busy timeout."""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def migrate(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA_PATH.read_text())
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(component, version) VALUES ('wallet', ?)",
                (SCHEMA_VERSION,),
            )
        finally:
            conn.close()

    @contextmanager
    def write_tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised read-write transaction; commits on success, rolls back on any error."""
        conn = self.connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise DatabaseBusy(str(exc)) from exc
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            try:
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                conn.execute("ROLLBACK")
                raise DatabaseBusy(str(exc)) from exc
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) or "busy" in str(exc):
                raise DatabaseBusy(str(exc)) from exc
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()
