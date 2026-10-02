"""Independent audit verifier: a separate process with its own data dir and pinned key.

    cd services/api
    .venv/bin/python -m mandate.audit.verifier --port 8201 \\
        --public-key .data/audit/keys/checkpoint_ed25519.pub.pem   # first run pins this key

It retains signed checkpoints in ``<data-dir>/checkpoints.sqlite3`` (default
``.data/verifier``), outside the wallet database, and checks exports against
them. A checkpoint carried inside an export is never trusted: only the one this
process retained under ``retained_checkpoint_id`` anchors history.

Internal endpoints (the API's ``/audit/checkpoints`` and ``/verifier/check``
forward here): ``POST /checkpoints``, ``POST /check``, ``GET /healthz``.
Assumption: it binds to 127.0.0.1 and needs no bearer token, since retaining
requires a valid signature under the pinned key and checking is read-only.

Limits: an operator can still hide events after the latest retained
checkpoint, and a hash chain does not prove every real action was logged.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .checkpoints import checkpoint_id, load_public_key, signature_valid
from .log import GENESIS_HASH, canonical_json, event_hash

PINNED_NAME = "pinned_checkpoint_key.pem"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / ".data" / "verifier"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AuditEvent(_Strict):
    stream_id: str
    sequence: int
    event_id: str
    type: str  # contract enum; kept open so a new wallet event type is verified, not rejected
    occurred_at: str
    actor_id: str
    mandate_id: str | None
    transaction_id: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


class Checkpoint(_Strict):
    stream_id: str
    sequence: int
    event_hash: str
    key_id: str
    created_at: str
    signature: str


class AuditExport(_Strict):
    format_version: str
    stream_id: str
    events: list[AuditEvent]
    latest_checkpoint: Checkpoint | None
    public_key_id: str


class VerifierRequest(_Strict):
    export: AuditExport
    retained_checkpoint_id: str


def verify(export: dict, checkpoint: dict | None, public_key: Ed25519PublicKey) -> dict:
    """VerifierResult for ``export`` against a checkpoint from the verifier's own store (or None)."""
    stream, events = export["stream_id"], export["events"]
    failures: list[dict] = []

    def fail(code: str, sequence: int | None, message: str) -> None:
        failures.append({"code": code, "sequence": sequence, "message": message})

    # Structural walk over the whole export, anchored or not.
    previous, expected = GENESIS_HASH, 1
    for e in events:
        seq = e["sequence"]
        if e["stream_id"] != stream:
            fail("STREAM_MISMATCH", seq, f"event belongs to {e['stream_id']!r}, export is {stream!r}")
        if seq != expected:
            fail("SEQUENCE_GAP", seq, f"expected sequence {expected}, found {seq}")
        if e["previous_hash"] != previous:
            fail("HASH_MISMATCH", seq, "previous_hash does not link to the prior event")
        try:
            recomputed = event_hash(e)
        except (TypeError, ValueError):
            recomputed = None  # e.g. a float smuggled into the payload: not canonical
        if recomputed != e["event_hash"]:
            fail("HASH_MISMATCH", seq, "event_hash does not match the event's canonical fields")
        previous, expected = e["event_hash"], seq + 1
    last = events[-1]["sequence"] if events else 0

    if checkpoint is None:
        return {
            "status": "no_trusted_checkpoint", "valid": False, "checked_through_sequence": 0,
            "export_last_sequence": last, "unanchored_event_count": len(events), "failures": failures,
            "message": "No retained checkpoint with that ID; the chain was only checked structurally, "
                       "which is not an independent pass.",
        }

    cp_seq = checkpoint["sequence"]
    if not signature_valid(checkpoint, public_key):
        fail("SIGNATURE_INVALID", cp_seq, "retained checkpoint does not verify under the pinned key")
    if checkpoint["stream_id"] != stream:
        fail("STREAM_MISMATCH", None, f"checkpoint is for {checkpoint['stream_id']!r}, export is {stream!r}")
    elif last < cp_seq:
        fail("TRUNCATED_BEFORE_CHECKPOINT", cp_seq, f"export ends at {last}, checkpoint covers {cp_seq}")
    else:
        covered = next((e for e in events if e["sequence"] == cp_seq), None)
        if covered is None or covered["event_hash"] != checkpoint["event_hash"]:
            fail("CHECKPOINT_MISMATCH", cp_seq, "export's event at the checkpoint differs from the retained hash")

    unanchored = sum(1 for e in events if e["sequence"] > cp_seq)
    if failures:
        return {
            "status": "invalid", "valid": False, "checked_through_sequence": 0,
            "export_last_sequence": last, "unanchored_event_count": unanchored, "failures": failures,
            "message": f"Export does not agree with retained checkpoint {checkpoint_id(checkpoint)}.",
        }
    return {
        "status": "valid_through_checkpoint", "valid": True, "checked_through_sequence": cp_seq,
        "export_last_sequence": last, "unanchored_event_count": unanchored, "failures": [],
        "message": f"History through sequence {cp_seq} matches retained checkpoint {checkpoint_id(checkpoint)}"
                   + (f"; {unanchored} later event(s) are structurally valid but unanchored." if unanchored else "."),
    }


class RetainedCheckpoints:
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS retained_checkpoints ("
                      "id TEXT PRIMARY KEY, body_json TEXT NOT NULL, retained_at TEXT NOT NULL)")

    def retain(self, checkpoint: dict) -> bool:
        """Store once; True if this exact checkpoint is now retained, False if the ID holds another."""
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("INSERT OR IGNORE INTO retained_checkpoints VALUES (?,?,?)",
                      (checkpoint_id(checkpoint), canonical_json(checkpoint).decode(),
                       datetime.now(timezone.utc).isoformat()))
            return self._get(c, checkpoint_id(checkpoint)) == checkpoint

    def get(self, cid: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as c:
            return self._get(c, cid)

    @staticmethod
    def _get(c: sqlite3.Connection, cid: str) -> dict | None:
        row = c.execute("SELECT body_json FROM retained_checkpoints WHERE id = ?", (cid,)).fetchone()
        return json.loads(row[0]) if row else None


def pin_public_key(data_dir: Path, public_key_path: str | Path | None) -> Ed25519PublicKey:
    """First run copies the key into the data dir; later runs refuse a different key."""
    pinned = data_dir / PINNED_NAME
    if not pinned.exists():
        if public_key_path is None:
            raise SystemExit(f"no pinned key in {data_dir}; pass --public-key on first run")
        pinned.write_bytes(Path(public_key_path).read_bytes())
    elif public_key_path is not None and Path(public_key_path).read_bytes() != pinned.read_bytes():
        raise SystemExit(f"--public-key differs from the key pinned in {pinned}; refusing to start")
    return load_public_key(pinned)


def create_app(data_dir: str | Path = DEFAULT_DATA_DIR, public_key_path: str | Path | None = None) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    public_key = pin_public_key(data_dir, public_key_path)
    store = RetainedCheckpoints(data_dir / "checkpoints.sqlite3")
    app = FastAPI(title="Mandate audit verifier", version="0.1.0")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/checkpoints", status_code=201)
    def retain(body: Checkpoint, response: Response):
        cp = body.model_dump()
        if not signature_valid(cp, public_key):
            raise HTTPException(422, "checkpoint signature does not verify under the pinned key")
        if store.get(checkpoint_id(cp)) == cp:
            response.status_code = 200
        elif not store.retain(cp):
            raise HTTPException(409, "a different checkpoint is already retained under this ID")
        return {"id": checkpoint_id(cp), "retained": True}

    @app.post("/check")
    def check(body: VerifierRequest):
        return verify(body.export.model_dump(), store.get(body.retained_checkpoint_id), public_key)

    return app


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8201)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--public-key", help="checkpoint public PEM to pin on first run")
    args = p.parse_args()
    uvicorn.run(create_app(args.data_dir, args.public_key), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
