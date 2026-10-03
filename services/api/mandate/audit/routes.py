"""Audit endpoints from contracts/openapi.json as one APIRouter.

The shared app mounts it next to the wallet router, on the same database:

    app.include_router(audit_routes.build_router(wallet.db), prefix="/api/v1")

``/events`` pages the caller's own stream for the activity feed. ``/audit/checkpoints`` signs the stream head, and only reports success after
the verifier process confirms it retained the checkpoint; ``/verifier/check``
forwards to that process. Users see only their own stream
(``stream_<user id>``, the wallet's naming); a ``verifier`` actor may check any export.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from mandate.payments.audit_shim import stream_for_owner
from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.errors import ApiError, conflict, not_found
from mandate.storage.db import Database

from .checkpoints import CheckpointSigner, checkpoint_id
from .log import ensure_schema, read_events, sha256_hex, stream_head
from .verifier import VerifierRequest

DEFAULT_KEY_DIR = Path(__file__).resolve().parents[2] / ".data" / "audit" / "keys"
DEFAULT_VERIFIER_URL = "http://127.0.0.1:8201"

DDL = """
CREATE TABLE IF NOT EXISTS audit_checkpoints (
    id          TEXT PRIMARY KEY,   -- <stream_id>:<sequence>
    stream_id   TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    body_json   TEXT NOT NULL,      -- the signed contract Checkpoint
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_checkpoint_requests (
    actor_id      TEXT NOT NULL,
    key           TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL REFERENCES audit_checkpoints(id),
    PRIMARY KEY (actor_id, key)
);
"""

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class CheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stream_id: str


def _role(*roles: str):
    def dep(actor: Actor = Depends(current_actor)) -> Actor:
        return require_role(actor, *roles)
    return dep


UserOnly = Annotated[Actor, Depends(_role("user"))]
UserOrVerifier = Annotated[Actor, Depends(_role("user", "verifier"))]


def _verifier_down() -> ApiError:
    return ApiError(503, "SERVICE_BUSY", "Independent verifier did not respond; nothing was retained or checked.",
                    retryable=True)


def _post(url: str, body: dict, timeout_s: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        raise _verifier_down() from None


def _own_stream(actor: Actor, stream_id: str | None) -> str:
    mine = stream_for_owner(actor.actor_id)
    if stream_id is not None and stream_id != mine:
        raise not_found("Audit stream")
    return mine


def _ensure(conn) -> None:
    ensure_schema(conn)
    conn.executescript(DDL)


def build_router(db: Database, signer: CheckpointSigner | None = None, verifier_url: str | None = None,
                 timeout_s: float = 5.0) -> APIRouter:
    signer = signer or CheckpointSigner.from_key_dir(os.environ.get("MANDATE_AUDIT_KEY_DIR") or DEFAULT_KEY_DIR)
    verifier_url = (verifier_url or os.environ.get("MANDATE_VERIFIER_URL") or DEFAULT_VERIFIER_URL).rstrip("/")
    with db.read() as conn:
        _ensure(conn)
    router = APIRouter(tags=["audit"])

    def stored_checkpoint(conn, cid: str) -> dict | None:
        row = conn.execute("SELECT body_json FROM audit_checkpoints WHERE id = ?", (cid,)).fetchone()
        return json.loads(row[0]) if row else None

    @router.get("/events")
    def get_events(actor: UserOnly, after: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=200)] = 50):
        with db.read() as conn:
            events = read_events(conn, stream_for_owner(actor.actor_id), after=after, limit=limit + 1)
        page = events[:limit]
        return {"events": page, "next_after": page[-1]["sequence"] if page else after,
                "has_more": len(events) > limit}

    @router.get("/audit/export")
    def export_audit(actor: UserOnly, stream_id: str | None = None):
        stream = _own_stream(actor, stream_id)
        with db.read() as conn:
            conn.execute("BEGIN")  # events and checkpoint from one snapshot
            events = read_events(conn, stream)
            row = conn.execute("SELECT body_json FROM audit_checkpoints WHERE stream_id = ? "
                               "ORDER BY sequence DESC LIMIT 1", (stream,)).fetchone()
            conn.execute("COMMIT")
        return {"format_version": "0.1.0", "stream_id": stream, "events": events,
                "latest_checkpoint": json.loads(row[0]) if row else None, "public_key_id": signer.key_id}

    @router.post("/audit/checkpoints", status_code=201)
    def create_checkpoint(actor: UserOnly, key: IdempotencyKey, body: CheckpointRequest):
        stream = _own_stream(actor, body.stream_id)
        request_hash = sha256_hex(body.model_dump())
        with db.read() as conn:
            prior = conn.execute("SELECT request_hash, checkpoint_id FROM audit_checkpoint_requests "
                                 "WHERE actor_id = ? AND key = ?", (actor.actor_id, key)).fetchone()
            if prior:
                if prior[0] != request_hash:
                    raise conflict("Idempotency-Key reused with a different request.", code="IDEMPOTENCY_CONFLICT")
                return JSONResponse(stored_checkpoint(conn, prior[1]), status_code=201)
            head = stream_head(conn, stream)
            if head is None:
                raise conflict("Stream has no events to checkpoint.")
            checkpoint = stored_checkpoint(conn, f"{stream}:{head[0]}") or signer.sign(stream, *head)

        # Outside any DB lock: the verifier must retain it before we report success.
        status, _ = _post(f"{verifier_url}/checkpoints", checkpoint, timeout_s)
        if status == 409:
            raise conflict("Verifier already retains a different checkpoint for this head; retry.")
        if status not in (200, 201):
            raise _verifier_down()

        cid = checkpoint_id(checkpoint)
        with db.write_tx() as conn:
            conn.execute("INSERT OR IGNORE INTO audit_checkpoints VALUES (?,?,?,?,?)",
                         (cid, stream, checkpoint["sequence"], json.dumps(checkpoint), checkpoint["created_at"]))
            conn.execute("INSERT OR IGNORE INTO audit_checkpoint_requests VALUES (?,?,?,?)",
                         (actor.actor_id, key, request_hash, cid))
        return JSONResponse(checkpoint, status_code=201)

    @router.post("/verifier/check")
    def verifier_check(actor: UserOrVerifier, body: VerifierRequest):
        if actor.role == "user":
            mine = _own_stream(actor, body.export.stream_id)
            if body.retained_checkpoint_id.rpartition(":")[0] != mine:
                raise not_found("Checkpoint")
        status, out = _post(f"{verifier_url}/check", body.model_dump(), timeout_s)
        if status != 200:
            raise _verifier_down()
        return out

    return router
