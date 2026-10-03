import hashlib
import math
import sqlite3

import pytest

from mandate.audit import GENESIS_HASH, append_event, canonical_json, event_hash, read_events
from mandate.payments import audit_shim


def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "a.sqlite3", isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def add(c, stream="stream_x", payload=None, **kw) -> int:
    return append_event(c, stream, "quote_created", payload or {"amount_minor": 30000},
                        actor_id="agent_1", mandate_id="mnd_1", transaction_id=None, **kw)


def test_shim_uses_real_module():
    assert audit_shim.USING_STUB is False


def test_canonical_json_rules():
    assert canonical_json({"b": 1, "a": {"d": [3, 1, 2], "c": None}}) == b'{"a":{"c":null,"d":[3,1,2]},"b":1}'
    assert canonical_json({"name": "쌀 5kg"}) == '{"name":"쌀 5kg"}'.encode("utf-8")
    for bad in (1.5, {"x": 0.0}, [math.nan], {"x": [math.inf]}, {1: "int key"}):
        with pytest.raises(TypeError):
            canonical_json(bad)


def test_events_chain_per_stream_from_genesis(tmp_path):
    c = conn(tmp_path)
    c.execute("BEGIN IMMEDIATE")
    assert [add(c), add(c), add(c, stream="stream_y"), add(c)] == [1, 2, 1, 3]
    c.execute("COMMIT")
    events = read_events(c, "stream_x")
    assert [e["sequence"] for e in events] == [1, 2, 3]
    previous = GENESIS_HASH
    for e in events:
        assert e["previous_hash"] == previous
        fields = {k: v for k, v in e.items() if k != "event_hash"}
        assert len(fields) == 10
        assert e["event_hash"] == hashlib.sha256(canonical_json(fields)).hexdigest() == event_hash(e)
        previous = e["event_hash"]
    assert read_events(c, "stream_y")[0]["previous_hash"] == GENESIS_HASH
    assert [e["sequence"] for e in read_events(c, "stream_x", after=1, limit=1)] == [2]


def test_refuses_outside_transaction_and_never_commits(tmp_path):
    c = conn(tmp_path)
    with pytest.raises(RuntimeError):
        add(c)
    c.execute("BEGIN IMMEDIATE")
    add(c)
    assert c.in_transaction  # still the caller's transaction
    c.execute("ROLLBACK")
    assert read_events(c, "stream_x") == []


def test_rejects_float_payload_and_naive_time(tmp_path):
    c = conn(tmp_path)
    c.execute("BEGIN IMMEDIATE")
    with pytest.raises(TypeError):
        add(c, payload={"amount": 300.0})
    with pytest.raises(ValueError):
        add(c, occurred_at="2026-10-07T10:00:00")
    c.execute("ROLLBACK")
