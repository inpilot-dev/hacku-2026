"""Hash-chained audit log written inside the caller's transaction (contracts/README.md section 7).

The wallet calls ``append_event`` on its own open SQLite connection, in the
same transaction as the financial change, so an event exists exactly when its
state change committed. This module never commits or rolls back.

Each event's hash is SHA-256 over the canonical JSON of its ten fields other
than ``event_hash``; ``previous_hash`` links it to the stream's prior event and
the first event links to 64 zeroes. Standard library only: ``audit_shim``
silently falls back to its stub if importing this module fails.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

GENESIS_HASH = "0" * 64
HKT = timezone(timedelta(hours=8), "Asia/Hong_Kong")

# Same columns as the wallet's stub table (wallet_stub_audit_events).
DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    stream_id      TEXT NOT NULL,
    sequence       INTEGER NOT NULL CHECK (sequence >= 1),
    event_id       TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL,
    occurred_at    TEXT NOT NULL,
    actor_id       TEXT NOT NULL,
    mandate_id     TEXT,
    transaction_id TEXT,
    payload_json   TEXT NOT NULL,
    previous_hash  TEXT NOT NULL,
    event_hash     TEXT NOT NULL,
    PRIMARY KEY (stream_id, sequence)
)
"""

HASHED_FIELDS = ("stream_id", "sequence", "event_id", "type", "occurred_at", "actor_id",
                 "mandate_id", "transaction_id", "payload", "previous_hash")


def _check(value: Any) -> None:
    """Reject what canonical JSON forbids; json.dumps alone would accept floats and coerce keys."""
    if isinstance(value, float):
        raise TypeError("floating-point values (including NaN and infinity) are not allowed in canonical JSON")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical JSON object keys must be strings, got {type(k).__name__}")
            _check(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check(v)


def canonical_json(value: Any) -> bytes:
    """Recursively sorted keys, compact separators, UTF-8, no floats/NaN/infinity, array order kept."""
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def event_hash(event: dict) -> str:
    """Hash of an AuditEvent dict; ``event_hash`` itself (and anything extra) is ignored."""
    return sha256_hex({k: event[k] for k in HASHED_FIELDS})


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)


def _now() -> str:
    return datetime.now(HKT).replace(microsecond=0).isoformat()


def append_event(
    conn: sqlite3.Connection,
    stream_id: str,
    event_type: str,
    payload: dict,
    *,
    actor_id: str,
    mandate_id: str | None,
    transaction_id: str | None,
    occurred_at: str | None = None,
) -> int:
    """Append one event on ``conn`` without committing; returns its sequence (1-based per stream)."""
    if not conn.in_transaction:
        raise RuntimeError("append_event must run inside the caller's open transaction")
    if not isinstance(payload, dict):
        raise TypeError("audit payload must be a JSON object")
    occurred_at = occurred_at or _now()
    if datetime.fromisoformat(occurred_at).tzinfo is None:
        raise ValueError("occurred_at needs an explicit UTC offset")
    head = stream_head(conn, stream_id)
    sequence, previous_hash = (head[0] + 1, head[1]) if head else (1, GENESIS_HASH)
    event = {
        "stream_id": stream_id,
        "sequence": sequence,
        "event_id": f"evt_{uuid.uuid4().hex}",
        "type": event_type,
        "occurred_at": occurred_at,
        "actor_id": actor_id,
        "mandate_id": mandate_id,
        "transaction_id": transaction_id,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    conn.execute(
        "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (stream_id, sequence, event["event_id"], event_type, occurred_at, actor_id, mandate_id,
         transaction_id, canonical_json(payload).decode("utf-8"), previous_hash, event_hash(event)),
    )
    return sequence


def _row_to_event(row) -> dict:
    keys = ("stream_id", "sequence", "event_id", "type", "occurred_at", "actor_id",
            "mandate_id", "transaction_id", "payload_json", "previous_hash", "event_hash")
    event = dict(zip(keys, tuple(row)))
    event["payload"] = json.loads(event.pop("payload_json"))
    return {k: event[k] for k in (*HASHED_FIELDS, "event_hash")}


def read_events(conn: sqlite3.Connection, stream_id: str, *, after: int = 0, limit: int | None = None) -> list[dict]:
    """Contract AuditEvent dicts in sequence order. Reusable by a future ``GET /events``."""
    ensure_schema(conn)
    sql = ("SELECT stream_id, sequence, event_id, type, occurred_at, actor_id, mandate_id, transaction_id, "
           "payload_json, previous_hash, event_hash FROM audit_events WHERE stream_id = ? AND sequence > ? "
           "ORDER BY sequence")
    params: tuple = (stream_id, after)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return [_row_to_event(r) for r in conn.execute(sql, params)]


def stream_head(conn: sqlite3.Connection, stream_id: str) -> tuple[int, str] | None:
    """(sequence, event_hash) of the stream's last event, or None if it is empty."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT sequence, event_hash FROM audit_events WHERE stream_id = ? ORDER BY sequence DESC LIMIT 1",
        (stream_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None
