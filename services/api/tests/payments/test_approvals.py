"""Escalation: a purchase that needs review waits for the owner, who can approve or deny, or it lapses."""

from __future__ import annotations

import uuid

from .conftest import AGENT, POLICY, USER, key

NEEDS_OK = {**POLICY, "approval_above_minor": 20000}  # HK$297 rice is over HK$200


def escalate(h, txn=None):
    m = h.confirm(NEEDS_OK)
    q = h.quote()
    txn = txn or str(uuid.uuid4())
    body = h.authorize(m["id"], q["id"], txn).json()
    assert body["status"] == "requires_review"
    return m, q, txn, body


def decide(h, approval_id, verb, headers=USER, **body):
    return h.client.post(f"/api/v1/approvals/{approval_id}/{verb}", headers={**headers, **key()}, json=body)


def test_review_opens_an_approval_request_for_the_owner(h):
    m, q, txn, body = escalate(h)
    a = body["approval_request"]
    assert (a["status"], a["amount_minor"], a["quote_id"]) == ("pending", 29700, q["id"])
    assert [v["code"] for v in a["reasons"]] == ["APPROVAL_REQUIRED"]
    # Bounded by the 10-minute quote lifetime, which is sooner than the 15-minute approval window.
    assert a["expires_at"] == q["expires_at"]
    listed = h.client.get("/api/v1/approvals?status=pending", headers=USER).json()["approvals"]
    assert [x["id"] for x in listed] == [a["id"]]
    assert h.budget(m["id"])[0]["reserved_minor"] == 0


def test_approval_lets_the_agent_retry_once(h):
    m, q, txn, body = escalate(h)
    res = decide(h, body["approval_request"]["id"], "approve", note="Fine, she needs rice")
    assert res.status_code == 200 and res.json()["approval"]["status"] == "approved"

    auth = h.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "approved"
    assert h.pay(auth).json()["status"] == "completed"
    assert h.client.get(f"/api/v1/approvals/{body['approval_request']['id']}",
                        headers=AGENT).json()["status"] == "used"
    # The grant is spent: a later retry replays the same approval, it does not reserve again.
    again = h.authorize(m["id"], q["id"], txn).json()
    assert again["reservation"]["id"] == auth["reservation"]["id"]


def test_approval_still_enforces_hard_limits(h):
    m, q, txn, body = escalate(h)
    decide(h, body["approval_request"]["id"], "approve")
    # Spend the week's budget elsewhere before the agent retries.
    with h.wallet.db.write_tx() as conn:
        conn.execute("UPDATE budget_periods SET paid_minor = limit_minor - 100")
    auth = h.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "refused"
    assert auth["violations"][0]["code"] == "PERIOD_BUDGET_EXCEEDED"


def test_denial_is_final(h):
    m, q, txn, body = escalate(h)
    res = decide(h, body["approval_request"]["id"], "deny", note="Too much")
    assert res.json()["approval"]["status"] == "denied"
    auth = h.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "refused" and auth["violations"][0]["code"] == "APPROVAL_DENIED"
    assert decide(h, body["approval_request"]["id"], "approve").status_code == 409


def test_unanswered_request_lapses_into_a_refusal(h):
    m, q, txn, body = escalate(h)
    h.clock.advance(minutes=11)
    pending = h.client.get("/api/v1/approvals?status=pending", headers=USER).json()["approvals"]
    assert pending == []
    auth = h.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "refused" and auth["violations"][0]["code"] == "APPROVAL_EXPIRED"
    assert auth["approval_request"]["status"] == "expired"
    assert decide(h, body["approval_request"]["id"], "approve").status_code == 409


def test_agents_cannot_approve(h):
    _, _, _, body = escalate(h)
    assert decide(h, body["approval_request"]["id"], "approve", headers=AGENT).status_code == 403


def test_approval_does_not_survive_a_mandate_change(h):
    m, q, txn, body = escalate(h)
    decide(h, body["approval_request"]["id"], "approve")
    with h.wallet.db.write_tx() as conn:  # simulate a new mandate version
        conn.execute("UPDATE mandates SET version = version + 1 WHERE id = ?", (m["id"],))
    auth = h.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "refused" and auth["violations"][0]["code"] == "MANDATE_VERSION_CHANGED"


def test_revoked_mandate_cannot_be_approved(h):
    m, _, _, body = escalate(h)
    h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={})
    assert decide(h, body["approval_request"]["id"], "approve").status_code == 409
