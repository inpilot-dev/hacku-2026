"""Checkpoints and verification against a real verifier process (own port 82xx, own data dir, pinned key).

Events come from the real wallet flow, so this also covers wallet -> audit_events.
"""

import copy
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mandate.audit import GENESIS_HASH, event_hash
from mandate.audit.checkpoints import PUBLIC_NAME, CheckpointSigner
from mandate.audit.routes import build_router
from mandate.audit.verifier import pin_public_key
from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts
from tests.payments.conftest import AGENT, USER, Harness, key

SERVICES_API = Path(__file__).resolve().parents[2]


def free_port() -> int:
    for port in range(8200, 8300):  # session A's port range
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port in 8200-8299")


@contextmanager
def verifier_process(data_dir: Path, public_key: Path):
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "mandate.audit.verifier", "--port", str(port),
                             "--data-dir", str(data_dir), "--public-key", str(public_key)], cwd=SERVICES_API)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                urllib.request.urlopen(f"{url}/healthz", timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("verifier process did not start")
                time.sleep(0.05)
        yield SimpleNamespace(url=url, pid=proc.pid)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def a(tmp_path):
    clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path / "wallet", clock=clock, draft_lookup=drafts)
    signer = CheckpointSigner.from_key_dir(tmp_path / "audit_keys")
    with verifier_process(tmp_path / "verifier", tmp_path / "audit_keys" / PUBLIC_NAME) as v:
        app.include_router(build_router(wallet.db, signer, v.url), prefix="/api/v1")
        h = Harness(TestClient(app), wallet, drafts, clock)
        yield SimpleNamespace(h=h, c=h.client, verifier=v, signer=signer, tmp=tmp_path)


def export(a) -> dict:
    res = a.c.get("/api/v1/audit/export", headers=USER)
    assert res.status_code == 200, res.text
    return res.json()


def checkpoint(a) -> dict:
    res = a.c.post("/api/v1/audit/checkpoints", headers={**USER, **key()}, json={"stream_id": "stream_user_demo"})
    assert res.status_code == 201, res.text
    return res.json()


def check(a, exp: dict, cid: str) -> dict:
    res = a.c.post("/api/v1/verifier/check", headers=USER, json={"export": exp, "retained_checkpoint_id": cid})
    assert res.status_code == 200, res.text
    out = res.json()
    print(f"\n[verifier pid {a.verifier.pid}] {cid}: {out['status']} "
          f"codes={[f['code'] for f in out['failures']]} unanchored={out['unanchored_event_count']}")
    return out


def rechain(events: list[dict]) -> None:
    """What a careful forger does: renumber and recompute every hash."""
    previous = GENESIS_HASH
    for i, e in enumerate(events, 1):
        e["sequence"], e["previous_hash"] = i, previous
        e["event_hash"] = previous = event_hash(e)


def codes(result) -> set[str]:
    return {f["code"] for f in result["failures"]}


def test_original_is_valid_and_later_events_are_unanchored(a):
    m = a.h.confirm()
    a.h.buy(m["id"])
    cp = checkpoint(a)
    assert (cp["stream_id"], cp["sequence"], cp["key_id"]) == ("stream_user_demo", 4, a.signer.key_id)

    exp = export(a)
    assert exp["latest_checkpoint"] == cp and exp["public_key_id"] == a.signer.key_id
    assert [e["type"] for e in exp["events"]] == ["mandate_confirmed", "quote_created",
                                                   "authorization_approved", "payment_completed"]
    r = check(a, exp, "stream_user_demo:4")
    assert (r["status"], r["valid"], r["checked_through_sequence"], r["unanchored_event_count"]) == \
        ("valid_through_checkpoint", True, 4, 0)

    a.h.buy(m["id"])  # three more events after the checkpoint
    r = check(a, export(a), "stream_user_demo:4")
    assert (r["status"], r["export_last_sequence"], r["unanchored_event_count"]) == ("valid_through_checkpoint", 7, 3)


def test_covered_modification_fails(a):
    a.h.buy(a.h.confirm()["id"])
    checkpoint(a)
    exp = export(a)

    edited = copy.deepcopy(exp)
    edited["events"][2]["payload"]["amount_minor"] = 1  # change without fixing hashes
    r = check(a, edited, "stream_user_demo:4")
    assert r["status"] == "invalid" and not r["valid"] and "HASH_MISMATCH" in codes(r)

    forged = copy.deepcopy(edited)
    rechain(forged["events"])  # internally consistent again...
    forged["latest_checkpoint"] = a.signer.sign("stream_user_demo", 4, forged["events"][-1]["event_hash"])
    r = check(a, forged, "stream_user_demo:4")  # ...but not with the retained checkpoint
    assert r["status"] == "invalid" and codes(r) == {"CHECKPOINT_MISMATCH"}


def test_covered_deletion_and_truncation_fail(a):
    a.h.buy(a.h.confirm()["id"])
    checkpoint(a)
    exp = export(a)

    deleted = copy.deepcopy(exp)
    del deleted["events"][1]
    r = check(a, deleted, "stream_user_demo:4")
    assert r["status"] == "invalid" and {"SEQUENCE_GAP", "HASH_MISMATCH"} <= codes(r)

    rechained = copy.deepcopy(deleted)
    rechain(rechained["events"])
    r = check(a, rechained, "stream_user_demo:4")
    assert r["status"] == "invalid" and codes(r) == {"TRUNCATED_BEFORE_CHECKPOINT"}

    truncated = copy.deepcopy(exp)
    truncated["events"] = truncated["events"][:2]
    r = check(a, truncated, "stream_user_demo:4")
    assert r["status"] == "invalid" and codes(r) == {"TRUNCATED_BEFORE_CHECKPOINT"}


def test_no_trusted_checkpoint_ignores_checkpoint_inside_export(a):
    a.h.buy(a.h.confirm()["id"])
    exp = export(a)
    assert exp["latest_checkpoint"] is None
    exp["latest_checkpoint"] = a.signer.sign("stream_user_demo", 4, exp["events"][-1]["event_hash"])
    r = check(a, exp, "stream_user_demo:4")
    assert (r["status"], r["valid"], r["checked_through_sequence"], r["unanchored_event_count"]) == \
        ("no_trusted_checkpoint", False, 0, 4)


def test_verifier_retains_only_pinned_key_signatures(a):
    other = CheckpointSigner.generate().sign("stream_user_demo", 4, "0" * 64)
    req = urllib.request.Request(f"{a.verifier.url}/checkpoints", data=json.dumps(other).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 422


def test_checkpoint_fails_when_verifier_is_unreachable(tmp_path):
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path / "wallet", clock=FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT)),
                             draft_lookup=drafts)
    signer = CheckpointSigner.from_key_dir(tmp_path / "audit_keys")
    app.include_router(build_router(wallet.db, signer, "http://127.0.0.1:9", timeout_s=0.5), prefix="/api/v1")
    h = Harness(TestClient(app), wallet, drafts, None)
    h.buy(h.confirm()["id"])
    res = h.client.post("/api/v1/audit/checkpoints", headers={**USER, **key()}, json={"stream_id": "stream_user_demo"})
    assert res.status_code == 503 and res.json()["error"]["retryable"] is True
    assert h.client.get("/api/v1/audit/export", headers=USER).json()["latest_checkpoint"] is None


def test_scope_idempotency_and_empty_stream(a):
    res = a.c.post("/api/v1/audit/checkpoints", headers={**USER, **key()}, json={"stream_id": "stream_user_demo"})
    assert res.status_code == 409  # nothing to checkpoint yet
    a.h.buy(a.h.confirm()["id"])

    assert a.c.get("/api/v1/audit/export", headers=AGENT).status_code == 403
    assert a.c.get("/api/v1/audit/export?stream_id=stream_someone_else", headers=USER).status_code == 404
    res = a.c.post("/api/v1/verifier/check", headers=USER,
                   json={"export": export(a), "retained_checkpoint_id": "stream_someone_else:1"})
    assert res.status_code == 404

    k = key()
    first = a.c.post("/api/v1/audit/checkpoints", headers={**USER, **k}, json={"stream_id": "stream_user_demo"})
    again = a.c.post("/api/v1/audit/checkpoints", headers={**USER, **k}, json={"stream_id": "stream_user_demo"})
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert checkpoint(a) == first.json()  # same head, new key: same checkpoint, verifier not in conflict


def test_verifier_refuses_a_swapped_key(tmp_path):
    first = CheckpointSigner.from_key_dir(tmp_path / "k1")
    CheckpointSigner.from_key_dir(tmp_path / "k2")
    pin_public_key(tmp_path, tmp_path / "k1" / PUBLIC_NAME)
    assert pin_public_key(tmp_path, None).public_bytes_raw() == first.public_key.public_bytes_raw()
    with pytest.raises(SystemExit):
        pin_public_key(tmp_path, tmp_path / "k2" / PUBLIC_NAME)


def test_events_page_the_users_own_stream(a):
    a.h.buy(a.h.confirm()["id"])
    first = a.c.get("/api/v1/events?limit=3", headers=USER).json()
    assert [e["sequence"] for e in first["events"]] == [1, 2, 3] and first["has_more"] and first["next_after"] == 3
    rest = a.c.get(f"/api/v1/events?after={first['next_after']}", headers=USER).json()
    assert rest == {"events": export(a)["events"][3:], "next_after": 4, "has_more": False}
    assert a.c.get("/api/v1/events?after=4", headers=USER).json() == {"events": [], "next_after": 4, "has_more": False}
    assert a.c.get("/api/v1/events", headers=AGENT).status_code == 403
    assert a.c.get("/api/v1/events?limit=201", headers=USER).status_code == 422
