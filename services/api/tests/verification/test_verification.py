import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mandate.payments.errors import install_error_handlers
from mandate.verification import build_router, run

EXAMPLE = {"max_steps": 8, "initial_available_minor": 40000,
           "purchase_amounts_minor": [30000, 30000], "timeout_ms": 3000}
USER = {"Authorization": "Bearer dev-user-token"}


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(build_router(), prefix="/api/v1")
    return TestClient(app)


def test_unsafe_finds_overspend_race(client):
    r = client.post("/api/v1/verification/runs", json={"variant": "unsafe", **EXAMPLE}, headers=USER)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "counterexample_found" and body["solver_result"] == "sat"
    steps = body["counterexample"]
    # both agents read HK$400 before either reserves, then both spend
    checks = [s for s in steps if s["action"] == "check"]
    assert {s["actor"] for s in checks} == {"Agent A", "Agent B"}
    assert all("checked HK$400 available" in s["explanation"] for s in checks)
    assert steps[-1]["paid_minor"] + steps[-1]["reserved_minor"] > 40000
    assert steps[-1]["remaining_minor"] < 0


def test_atomic_has_no_counterexample(client):
    r = client.post("/api/v1/verification/runs", json={"variant": "atomic", **EXAMPLE}, headers=USER)
    body = r.json()
    assert body["status"] == "no_counterexample_within_bound" and body["solver_result"] == "unsat"
    assert body["counterexample"] == []
    assert "No counterexample found within the stated model and bound" in body["message"]
    assert len(body["checked_properties"]) == 4 and body["assumptions"]


def test_atomic_when_both_fit_is_still_safe():
    assert run("atomic", 60000, [30000, 30000])["status"] == "no_counterexample_within_bound"
    # unsafe with enough budget for both cannot overspend either
    assert run("unsafe", 60000, [30000, 30000])["status"] == "no_counterexample_within_bound"


def test_too_few_steps_cannot_race():
    # the race needs check, check, reserve, reserve: 3 steps are not enough
    assert run("unsafe", 40000, [30000, 30000], max_steps=3)["status"] == "no_counterexample_within_bound"


def test_tiny_timeout_is_inconclusive_or_answers():
    body = run("unsafe", 40000, [30000, 30000], max_steps=12, timeout_ms=100)
    assert body["status"] in {"inconclusive", "counterexample_found"}
    if body["status"] == "inconclusive":
        assert body["solver_result"] in {"timeout", "unknown"}


def test_auth_and_validation(client):
    url = "/api/v1/verification/runs"
    assert client.post(url, json={"variant": "atomic", **EXAMPLE}).status_code == 401
    agent = {"Authorization": "Bearer dev-agent-token"}
    assert client.post(url, json={"variant": "atomic", **EXAMPLE}, headers=agent).status_code == 403
    bad = {**EXAMPLE, "variant": "atomic", "purchase_amounts_minor": [1]}
    assert client.post(url, json=bad, headers=USER).status_code == 422
    assert client.post(url, json={**EXAMPLE, "variant": "atomic", "extra": 1}, headers=USER).status_code == 422
