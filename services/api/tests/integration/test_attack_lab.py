"""Security lab: every attack must be stopped by the real wallet, in an isolated sandbox."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mandate.integration.attack_lab import ATTACKS, build_attack_lab_router, run_attack
from mandate.payments.errors import install_error_handlers

USER = {"Authorization": "Bearer dev-user-token"}


@pytest.mark.parametrize("attack_id", [a.id for a in ATTACKS])
def test_every_attack_is_held(attack_id):
    result = run_attack(attack_id)
    assert result["error"] is None
    assert result["held"], (result["observed"], result["expected"])
    assert result["sandbox"]["isolated"]
    assert result["steps"] and any(step["phase"] == "attack" for step in result["steps"])


def test_trace_never_contains_a_usable_capability():
    result = run_attack("forged-token")
    for step in result["steps"]:
        for blob in (step["request"], step["response"]):
            token = (blob or {}).get("authorization_token")
            assert token is None or token.startswith("‹redacted")


def test_refused_attacks_move_no_money():
    for attack_id in ("over-order-cap", "forged-token", "quote-swap", "revoked-mid-flight", "privilege-escalation"):
        assert run_attack(attack_id)["ledger"]["paid_total_minor"] == 0, attack_id


def test_swarm_never_overspends_the_shared_budget():
    ledger = run_attack("agent-swarm")["ledger"]
    assert ledger["week_paid_minor"] <= ledger["week_limit_minor"]


def _client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(build_attack_lab_router(), prefix="/api/v1")
    return TestClient(app)


def test_lab_is_disabled_without_demo_flag(monkeypatch):
    monkeypatch.delenv("MANDATE_ENABLE_DEMO_CHECKOUT", raising=False)
    assert _client().get("/api/v1/demo/attacks", headers=USER).status_code == 403


def test_lab_is_user_only(monkeypatch):
    monkeypatch.setenv("MANDATE_ENABLE_DEMO_CHECKOUT", "1")
    client = _client()
    assert client.get("/api/v1/demo/attacks", headers={"Authorization": "Bearer dev-agent-token"}).status_code == 403
    assert client.post("/api/v1/demo/attacks/nope/runs", headers=USER).status_code == 404
    assert len(client.get("/api/v1/demo/attacks", headers=USER).json()["attacks"]) == len(ATTACKS)
