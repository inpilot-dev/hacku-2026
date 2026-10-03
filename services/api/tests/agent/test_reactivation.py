"""Switching an allowance back on after a freeze, as the simple UI does it."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from mandate.payments.clock import HKT, iso

USER = {"Authorization": "Bearer dev-user-token"}
CATALOG = Path(__file__).resolve().parents[4] / "data" / "catalog" / "wellcome.json"


def post(client, path, body):
    return client.post(f"/api/v1{path}", headers={**USER, "Idempotency-Key": str(uuid.uuid4())}, json=body)


def test_frozen_allowance_can_be_switched_back_on_with_a_fresh_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDATE_WALLET_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MANDATE_WALLET_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("MANDATE_CATALOG_PATH", str(CATALOG))
    from mandate.app import build_app

    client = TestClient(build_app())
    policy = {
        "currency": "HKD", "per_order_limit_minor": 30000,
        "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
        "allowed_merchant_ids": ["wellcome"], "blocked_categories": ["alcohol"],
        "expires_at": iso(datetime.now(HKT) + timedelta(days=6)), "approval_above_minor": None,
    }
    text = "Weekly allowance for Mum: HK$300 per order and HK$800 per week, only from wellcome, no alcohol."

    first_draft = post(client, "/mandates/draft", {"text": text, "delegatee_id": "agent_student"}).json()["draft_id"]
    first = post(client, "/mandates/confirm", {"draft_id": first_draft, "policy": policy})
    assert first.status_code == 201, first.text
    frozen = post(client, f"/mandates/{first.json()['id']}/revoke", {})
    assert frozen.status_code == 200, frozen.text

    # Reusing the confirmed draft is still refused: one draft backs one mandate.
    assert post(client, "/mandates/confirm", {"draft_id": first_draft, "policy": policy}).status_code == 409

    second_draft = post(client, "/mandates/draft", {"text": text, "delegatee_id": "agent_student"}).json()["draft_id"]
    second = post(client, "/mandates/confirm", {"draft_id": second_draft, "policy": policy})
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "active" and second.json()["id"] != first.json()["id"]
