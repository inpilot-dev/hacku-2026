"""Bridge to Seungbin's audit module (``mandate.audit``).

The wallet calls ``append_event`` on its own open connection, inside the same
transaction as the financial change, and never commits from here. Until the
real module lands this falls back to a stub with the same hash-chain shape,
writing to ``wallet_stub_audit_events`` so it can't collide with Seungbin's
``audit_events`` table.

Agreed interface (docs/proposals-seungbin.md) is
``append_event(conn, stream_id, event_type, payload)``. The contract's
AuditEvent also needs ``actor_id``, ``mandate_id`` and ``transaction_id``, so
the wallet passes those as keyword arguments; flagged to the team.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from .clock import SystemClock, iso

GENESIS_HASH = "0" * 64

try:  # pragma: no cover - exercised once Seungbin's module exists
    from mandate.audit import append_event as _real_append_event  # type: ignore[attr-defined]
    from mandate.audit import canonical_json as _real_canonical_json  # type: ignore[attr-defined]
except ImportError:
    _real_append_event = None
    _real_canonical_json = None

USING_STUB = _real_append_event is None


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("floating-point values are not allowed in canonical JSON")
    if isinstance(value, dict):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)


def _stub_canonical_json(value: Any) -> bytes:
    """Sorted keys, compact separators, UTF-8, no NaN/inf, no floats; array order kept."""
    _reject_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_json(value: Any) -> bytes:
    if _real_canonical_json is not None:
        out = _real_canonical_json(value)
        return out.encode("utf-8") if isinstance(out, str) else out
    return _stub_canonical_json(value)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


_STUB_DDL = """
CREATE TABLE IF NOT EXISTS wallet_stub_audit_events (
    stream_id      TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
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


def _stub_append_event(
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
    if not conn.in_transaction:
        raise RuntimeError("append_event must run inside the caller's open transaction")
    conn.execute(_STUB_DDL)
    row = conn.execute(
        "SELECT sequence, event_hash FROM wallet_stub_audit_events "
        "WHERE stream_id = ? ORDER BY sequence DESC LIMIT 1",
        (stream_id,),
    ).fetchone()
    sequence, previous_hash = (row[0] + 1, row[1]) if row else (1, GENESIS_HASH)
    event = {
        "stream_id": stream_id,
        "sequence": sequence,
        "event_id": f"evt_{uuid.uuid4().hex}",
        "type": event_type,
        "occurred_at": occurred_at or iso(SystemClock().now()),
        "actor_id": actor_id,
        "mandate_id": mandate_id,
        "transaction_id": transaction_id,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    event_hash = sha256_hex(event)
    conn.execute(
        "INSERT INTO wallet_stub_audit_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            stream_id, sequence, event["event_id"], event_type, event["occurred_at"],
            actor_id, mandate_id, transaction_id,
            canonical_json(payload).decode("utf-8"), previous_hash, event_hash,
        ),
    )
    return sequence


def append_event(conn: sqlite3.Connection, stream_id: str, event_type: str, payload: dict, **meta) -> int:
    """Append one audit event on ``conn`` without committing; returns its sequence."""
    if _real_append_event is not None:  # pragma: no cover
        return _real_append_event(conn, stream_id, event_type, payload, **meta)
    return _stub_append_event(conn, stream_id, event_type, payload, **meta)


def stream_for_owner(owner_id: str) -> str:
    return f"stream_{owner_id}"
