"""Local-only security lab: replay named attacks against a disposable wallet.

Every run builds a fresh in-process wallet (its own SQLite file, signing key,
card issuer and sandbox token table in a temporary directory, placeholder
catalog, fixed clock) and drives it through the ordinary HTTP routes with
user, agent and outsider credentials. Nothing is shared with the live wallet
and nothing is simulated: each refusal below is the real wallet code
answering a real request. The temporary directory is removed afterwards.

Bearer capabilities are redacted in the trace (only a short signature tail is
shown so a forged token can be compared with the original).
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Header
from fastapi.testclient import TestClient

from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.catalog import DEFAULT_CATALOG, Catalog
from mandate.payments.clock import HKT, FixedClock, iso
from mandate.payments.drafts import InMemoryDrafts
from mandate.payments.errors import forbidden, install_error_handlers, not_found, unauthenticated
from mandate.payments.issuing import SandboxCardIssuer
from mandate.payments.routes import build_router as build_wallet_router
from mandate.payments.service import Wallet
from mandate.payments.signing import Signer
from mandate.storage.db import Database

from .attack_lab_models import AttackList, AttackResult

# Sandbox-only credentials. They exist only inside the disposable wallet.
_SANDBOX_ACTORS = {
    "lab-user": Actor("user_demo", "user"),
    "lab-agent": Actor("agent_student", "agent", owner_id="user_demo"),
    "lab-stranger": Actor("user_stranger", "user"),
    "lab-stranger-agent": Actor("agent_stranger", "agent", owner_id="user_stranger"),
}
_ACTOR_LABELS = {"lab-user": "user", "lab-agent": "agent", "lab-stranger": "stranger",
                 "lab-stranger-agent": "stranger_agent"}

# Mum's weekly HK$800, HK$300 per order, no alcohol, shops A and B (contracts example policy).
POLICY = {
    "currency": "HKD",
    "per_order_limit_minor": 30000,
    "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
    "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
    "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00",
    "approval_above_minor": None,
}


def _basket(*items: tuple[str, int], merchant="demo_store_a", ctx="ctx_a_standard") -> dict:
    return {"merchant_id": merchant, "items": [{"product_id": p, "quantity": n} for p, n in items],
            "delivery_context_id": ctx}


# Placeholder catalog (not observed shop data): store A delivery is HK$30 under HK$400.
NORMAL_BASKET = _basket(("p_a_rice", 3))                                    # HK$297.00
RACE_BASKET = _basket(("p_a_rice", 1), ("p_a_milk", 1), ("p_a_apples", 5))  # HK$300.00
PRESPEND = [_basket(("p_a_rice", 2)), _basket(("p_a_rice", 1), ("p_a_milk", 2))]  # HK$400.00


class Lab:
    """One disposable wallet plus a recorder for every request made against it."""

    def __init__(self, tmp: str):
        root = Path(tmp)
        self.clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))  # a Wednesday in HKT
        self.drafts = InMemoryDrafts()
        db = Database(root / "wallet.sqlite3")
        db.migrate()
        self.wallet = Wallet(db, Signer.from_key_dir(root / "keys"), Catalog.load(DEFAULT_CATALOG),
                             clock=self.clock, draft_lookup=self.drafts,
                             issuer=SandboxCardIssuer.from_key_dir(root / "keys"))
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(build_wallet_router(self.wallet), prefix="/api/v1")
        app.dependency_overrides[current_actor] = _sandbox_actor
        self.client = TestClient(app)
        self.steps: list[dict] = []
        self._lock = threading.Lock()

    # --- recorded requests --------------------------------------------------------------------

    def call(self, phase: str, who: str, title: str, method: str, path: str, body: dict | None = None,
             idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {who}"}
        if method == "POST":
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        res = self.client.request(method, f"/api/v1{path}", headers=headers, json=body)
        data = res.json() if res.content else {}
        with self._lock:
            self.steps.append({
                "phase": phase, "actor": _label(who), "title": title, "method": method,
                "path": path, "request": _redact(body), "http_status": res.status_code,
                "outcome": _outcome(res.status_code, data), "response": _summary(data),
            })
        return {"http_status": res.status_code, **data}

    def confirm(self, policy: dict = POLICY, delegatee: str = "agent_student", parent: str | None = None,
                title: str = "Owner confirms the allowance") -> str:
        draft = f"draft_{uuid.uuid4()}"
        self.drafts.register(draft, owner_id="user_demo", delegatee_id=delegatee,
                             expires_at="2026-10-31T23:59:59+08:00", parent_mandate_id=parent)
        out = self.call("setup", "lab-user", title, "POST", "/mandates/confirm",
                        {"draft_id": draft, "policy": policy})
        assert out["http_status"] == 201, out
        return out["id"]

    def quote(self, body: dict, phase="setup", title="Agent asks the wallet to price the basket",
              who="lab-agent") -> dict:
        return self.call(phase, who, title, "POST", "/quotes", body)

    def authorize(self, mandate_id: str, quote_id: str, phase="setup", title="Agent requests authorization",
                  transaction_id: str | None = None, who="lab-agent") -> dict:
        return self.call(phase, who, title, "POST", "/authorizations",
                         {"transaction_id": transaction_id or str(uuid.uuid4()), "mandate_id": mandate_id,
                          "quote_id": quote_id})

    def pay(self, approved: dict, phase="attack", title="Agent submits payment", idempotency_key=None,
            who="lab-agent", **override) -> dict:
        body = {"transaction_id": approved["transaction_id"], "quote_id": approved["claims"]["quote_id"],
                "authorization_token": approved["authorization_token"], **override}
        return self.call(phase, who, title, "POST", "/payments", body, idempotency_key)

    def buy(self, mandate_id: str, body: dict, title: str, who="lab-agent") -> dict:
        auth = self.authorize(mandate_id, self.quote(body, who=who)["id"], title=f"{title}: authorize", who=who)
        assert auth["status"] == "approved", auth
        paid = self.pay(auth, phase="setup", title=f"{title}: pay", who=who)
        assert paid["status"] == "completed", paid
        return paid

    # --- ledger evidence (read straight from the wallet, not from the trace) -------------------

    def ledger(self, mandate_id: str | None) -> dict:
        with closing(self.wallet.db.connect()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
            moved = conn.execute("SELECT COALESCE(SUM(json_extract(receipt_json, '$.amount_minor')), 0) "
                                 "FROM payments").fetchone()[0]
        week = None
        if mandate_id:
            budgets = self.wallet.budget(_SANDBOX_ACTORS["lab-user"], mandate_id)["applicable_budgets"]
            week = next((b for b in budgets if b["mandate_id"] == mandate_id and b["period"] == "calendar_week"),
                        None)
        return {"completed_payments": int(count), "paid_total_minor": int(moved),
                "week_limit_minor": week["limit_minor"] if week else None,
                "week_paid_minor": week["paid_minor"] if week else None,
                "week_reserved_minor": week["reserved_minor"] if week else None}


SWARM_SIZE = 8


def _actor_for(token: str) -> Actor | None:
    """Fixed sandbox actors plus ``lab-swarm-<n>``: independent agents of the same household."""
    if token.startswith("lab-swarm-") and token.removeprefix("lab-swarm-").isdigit():
        n = int(token.removeprefix("lab-swarm-"))
        return Actor(f"agent_swarm_{n}", "agent", owner_id="user_demo") if 1 <= n <= SWARM_SIZE else None
    return _SANDBOX_ACTORS.get(token)


def _label(token: str) -> str:
    return _ACTOR_LABELS.get(token) or f"swarm_agent_{token.removeprefix('lab-swarm-')}"


def _sandbox_actor(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthenticated()
    actor = _actor_for(authorization.removeprefix("Bearer ").strip())
    if actor is None:
        raise unauthenticated()
    return actor


def _redact(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "authorization_token" and isinstance(v, str):
                out[k] = f"‹redacted …{v[-8:]}›"
            elif k in {"payment_credential", "card_number", "cvc", "pan"}:
                out[k] = "‹redacted›"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _outcome(http_status: int, data: dict) -> str:
    if http_status >= 400:
        return f"HTTP {http_status} {(data.get('error') or {}).get('code', '')}".strip()
    status = data.get("status")
    if status in {"refused", "requires_review"} and data.get("violations"):
        return f"{status}:{data['violations'][0]['code']}"
    if status == "completed" and data.get("replayed"):
        return "completed (replayed original receipt)"
    return status or f"HTTP {http_status}"


def _summary(data: dict) -> dict:
    """Only the fields a reviewer needs; never a bearer capability."""
    keep = {}
    for k in ("id", "status", "total_minor", "final_amount_minor", "replayed", "transaction_id", "message"):
        if k in data:
            keep[k] = data[k]
    if "error" in data:
        keep["error"] = {k: data["error"].get(k) for k in ("code", "message")}
    if data.get("violations"):
        keep["violations"] = [{k: v.get(k) for k in ("code", "rule_id", "message", "actual_minor", "limit_minor")
                               if v.get(k) is not None} for v in data["violations"]]
    if data.get("receipt"):
        keep["receipt"] = {k: data["receipt"].get(k) for k in ("id", "amount_minor", "status", "payment_mode")}
    if data.get("authorization_token"):
        keep["authorization_token"] = _redact({"authorization_token": data["authorization_token"]})[
            "authorization_token"]
    return keep


# --- attacks -----------------------------------------------------------------------------------
# Each returns (observed, expected, mandate_id). ``observed`` is derived from wallet responses.

def control(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"], phase="attack",
                         title="Agent requests a HK$297 rice basket within every rule")
    paid = lab.pay(auth, title="Agent pays with the capability it was issued")
    return _final(paid), "completed", m


def over_order_cap(lab: Lab):
    m = lab.confirm()
    q = lab.quote(_basket(("p_a_apples", 10)), phase="attack",
                  title="Agent prices HK$289 of apples (the HK$30 delivery fee makes it HK$319)")
    auth = lab.authorize(m, q["id"], phase="attack", title="Agent tries to authorize HK$319 against a HK$300 cap")
    return _final(auth), "refused:ORDER_CAP_EXCEEDED", m


def blocked_category(lab: Lab):
    m = lab.confirm()
    q = lab.quote(_basket(("p_a_beer", 1)), phase="attack", title="Agent adds a lager 6-pack")
    auth = lab.authorize(m, q["id"], phase="attack", title="Agent tries to buy alcohol under an alcohol block")
    return _final(auth), "refused:CATEGORY_BLOCKED", m


def blocked_merchant(lab: Lab):
    m = lab.confirm()
    q = lab.quote(_basket(("p_c_snacks", 1), merchant="demo_store_c", ctx="ctx_c_standard"), phase="attack",
                  title="Agent shops at store C, which the owner never approved")
    auth = lab.authorize(m, q["id"], phase="attack", title="Agent tries to pay store C")
    return _final(auth), "refused:MERCHANT_NOT_ALLOWED", m


def price_injection(lab: Lab):
    m = lab.confirm()
    body = {**_basket(("p_a_rice", 3)), "total_minor": 100}
    body["items"][0]["unit_price_minor"] = 1
    out = lab.quote(body, phase="attack", title="Agent submits its own prices (HK$0.01 rice, HK$1 total)")
    return _final(out), "HTTP 422 INVALID_REQUEST", m


def forged_token(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"])
    tok = auth["authorization_token"]
    forged = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    paid = lab.pay(auth, title="Agent pays with a token whose signature was edited", authorization_token=forged)
    return _final(paid), "refused:AUTHORIZATION_INVALID", m


def quote_swap(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"], title="Agent gets HK$297 rice basket approved")
    other = lab.quote(_basket(("p_a_rice", 1), ("p_a_milk", 2)), phase="attack",
                      title="Agent prices a different basket")
    paid = lab.pay(auth, title="Agent presents the approved token with the other basket", quote_id=other["id"])
    return _final(paid), "refused:QUOTE_CHANGED", m


def double_spend(lab: Lab):
    m = lab.confirm()
    for i, body in enumerate(PRESPEND, 1):
        lab.buy(m, body, f"Earlier shop {i}")
    quotes = [lab.quote(RACE_BASKET)["id"] for _ in range(2)]
    results: list[dict | None] = [None, None]
    barrier = threading.Barrier(2)

    def go(i: int):
        barrier.wait()
        results[i] = lab.authorize(m, quotes[i], phase="attack",
                                   title=f"Racing request {i + 1}: HK$300 with HK$400 left")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    finals = [lab.pay(r, title="Winner pays") if r and r["status"] == "approved" else r for r in results]
    outcomes = sorted(_final(f) for f in finals)
    return " + ".join(outcomes), "completed + refused:PERIOD_BUDGET_EXCEEDED", m


def replay(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"])
    k = str(uuid.uuid4())
    first = lab.pay(auth, title="Agent pays once", idempotency_key=k)
    second = lab.pay(auth, title="Agent replays the same request", idempotency_key=k)
    third = lab.pay(auth, title="Agent replays with a fresh idempotency key")
    ids = {r.get("receipt", {}).get("id") for r in (first, second, third)}
    observed = "one receipt" if len(ids) == 1 and None not in ids else f"{len(ids)} receipts"
    return observed, "one receipt", m


def revoked_mid_flight(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"])
    lab.call("attack", "lab-user", "Owner revokes the allowance", "POST", f"/mandates/{m}/revoke",
             {"reason": "Changed my mind"})
    paid = lab.pay(auth, title="Agent still tries to use its earlier approval")
    return _final(paid), "refused:MANDATE_REVOKED", m


def expired_capability(lab: Lab):
    m = lab.confirm()
    auth = lab.authorize(m, lab.quote(NORMAL_BASKET)["id"])
    lab.clock.advance(seconds=121)
    paid = lab.pay(auth, title="Agent pays 121 s later (capabilities live 120 s)")
    return _final(paid), "refused:AUTHORIZATION_EXPIRED", m


def privilege_escalation(lab: Lab):
    m = lab.confirm()
    draft = f"draft_{uuid.uuid4()}"
    lab.drafts.register(draft, owner_id="user_demo", delegatee_id="agent_student",
                        expires_at="2026-10-31T23:59:59+08:00")
    out = lab.call("attack", "lab-agent", "Agent tries to confirm a HK$1,000,000 allowance for itself", "POST",
                   "/mandates/confirm", {"draft_id": draft, "policy": {
                       **POLICY, "per_order_limit_minor": 100_000_000, "blocked_categories": [],
                       "period_limits": [{"period": "calendar_week", "limit_minor": 100_000_000,
                                          "timezone": "Asia/Hong_Kong"}]}})
    return _final(out), "HTTP 403 FORBIDDEN", m


def cross_family(lab: Lab):
    m = lab.confirm()
    seen = lab.call("attack", "lab-stranger", "Another household reads your allowance", "GET", f"/mandates/{m}")
    q = lab.call("attack", "lab-stranger-agent", "Their agent prices a basket", "POST", "/quotes", NORMAL_BASKET)
    auth = lab.authorize(m, q["id"], phase="attack", who="lab-stranger-agent",
                         title="Their agent tries to spend from your allowance")
    return f"{_final(seen)} + {_final(auth)}", "HTTP 404 NOT_FOUND + HTTP 404 NOT_FOUND", m


def agent_swarm(lab: Lab):
    root = lab.confirm(delegatee="agent_coordinator", title="Owner confirms one HK$800/week household budget")
    children = [lab.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, delegatee=f"agent_swarm_{n}",
                            parent=root, title=f"Owner delegates a sub-allowance to agent {n}")
                for n in range(1, SWARM_SIZE + 1)]
    for i, body in enumerate(PRESPEND, 1):
        lab.buy(children[0], body, f"Earlier shop {i} by agent 1", who="lab-swarm-1")
    quotes = [lab.quote(RACE_BASKET, who=f"lab-swarm-{n}", title=f"Agent {n} prices a HK$300 basket")["id"]
              for n in range(1, SWARM_SIZE + 1)]
    results: list[dict | None] = [None] * SWARM_SIZE
    barrier = threading.Barrier(SWARM_SIZE)

    def go(i: int):
        barrier.wait()
        results[i] = lab.authorize(children[i], quotes[i], phase="attack", who=f"lab-swarm-{i + 1}",
                                   title=f"Agent {i + 1} requests HK$300 at the same instant")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(SWARM_SIZE)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    finals = [lab.pay(r, title=f"Agent {i + 1} pays", who=f"lab-swarm-{i + 1}")
              if r and r["status"] == "approved" else r for i, r in enumerate(results)]
    completed = sum(_final(f) == "completed" for f in finals)
    refused = sum(_final(f) == "refused:PERIOD_BUDGET_EXCEEDED" for f in finals)
    return (f"{completed} completed, {refused} refused:PERIOD_BUDGET_EXCEEDED",
            f"1 completed, {SWARM_SIZE - 1} refused:PERIOD_BUDGET_EXCEEDED", root)


def sibling_agent(lab: Lab):
    m = lab.confirm()
    q = lab.quote(NORMAL_BASKET, phase="attack", who="lab-swarm-1",
                  title="A second agent in the same household prices a basket")
    auth = lab.authorize(m, q["id"], phase="attack", who="lab-swarm-1",
                         title="It tries to spend the allowance delegated to the first agent")
    return _final(auth), "HTTP 404 NOT_FOUND", m


def _final(response: dict | None) -> str:
    if response is None:
        return "no response"
    return _outcome(response["http_status"], response)


@dataclass(frozen=True)
class Attack:
    id: str
    title: str
    category: str
    threat: str
    defence: str
    run: Callable[[Lab], tuple[str, str, str | None]]


ATTACKS = [
    Attack("control", "Legitimate purchase (control)", "control",
           "A lab that refuses everything proves nothing.",
           "An in-policy HK$297 basket is approved and paid once.", control),
    Attack("over-order-cap", "Hidden fee pushes the order over its cap", "spending_limits",
           "The agent picks items under HK$300, but delivery adds HK$30.",
           "The wallet prices the whole basket itself, including fees, and refuses HK$319.", over_order_cap),
    Attack("blocked-category", "Agent buys alcohol", "policy",
           "A compromised or confused agent adds beer to the basket.",
           "Categories come from the trusted catalog, not from the agent.", blocked_category),
    Attack("blocked-merchant", "Agent shops at an unapproved store", "policy",
           "The agent is steered to a shop the owner never allowed.",
           "Only merchants on the confirmed allowance can be paid.", blocked_merchant),
    Attack("price-injection", "Agent invents its own prices", "integrity",
           "A prompt-injected agent claims rice costs one cent.",
           "Unknown fields are rejected; prices only come from the wallet's catalog adapter.", price_injection),
    Attack("forged-token", "Forged payment capability", "integrity",
           "The agent edits its signed authorization to try to reuse or widen it.",
           "Ed25519 signature check fails before any state is touched.", forged_token),
    Attack("quote-swap", "Bait-and-switch basket", "integrity",
           "Approval is obtained for one basket, then used to pay for another.",
           "The capability is bound to one quote hash; a different quote is refused.", quote_swap),
    Attack("double-spend", "Concurrent double spend", "concurrency",
           "Two HK$300 requests race for the last HK$400 of the weekly budget.",
           "Reservations are atomic: exactly one wins, the other is refused.", double_spend),
    Attack("replay", "Payment replayed three times", "concurrency",
           "A retry storm or malicious replay tries to charge the same basket repeatedly.",
           "Payments are keyed by transaction; replays return the original receipt and never debit again.",
           replay),
    Attack("revoked-mid-flight", "Revoked between approval and payment", "revocation",
           "The owner revokes access, but the agent already holds an approval.",
           "Payment re-checks live mandate state; revocation wins immediately.", revoked_mid_flight),
    Attack("expired-capability", "Stale capability", "revocation",
           "An approval is held back and used later.",
           "Capabilities expire after 120 seconds and the reservation is re-checked.", expired_capability),
    Attack("privilege-escalation", "Agent grants itself a bigger allowance", "access_control",
           "The agent calls the owner-only endpoint to raise its own limits.",
           "Roles come from the server's token table; agents cannot confirm mandates.", privilege_escalation),
    Attack("cross-family", "Another household tries to spend your allowance", "access_control",
           "A different user and their agent probe your mandate ID.",
           "Every lookup is scoped to the caller's family; foreign IDs are invisible.", cross_family),
    Attack("sibling-agent", "One agent borrows another agent's allowance", "access_control",
           "In a multi-agent system, a second agent reuses a mandate ID it overheard.",
           "Authority is bound to one delegatee; other agents, even in the same household, cannot use it.",
           sibling_agent),
    Attack("agent-swarm", f"{SWARM_SIZE} agents race one shared budget", "scale",
           f"{SWARM_SIZE} independent agents, each with its own sub-allowance, spend HK$300 at once with HK$400 left.",
           "Every ancestor budget is reserved atomically, so a swarm cannot overspend the parent.", agent_swarm),
]
_BY_ID = {a.id: a for a in ATTACKS}
_RUN_LOCK = threading.Lock()


def run_attack(attack_id: str) -> dict:
    attack = _BY_ID[attack_id]
    with _RUN_LOCK, tempfile.TemporaryDirectory(prefix="mandate-lab-") as tmp:
        lab = Lab(tmp)
        error = None
        try:
            observed, expected, mandate_id = attack.run(lab)
        except Exception as exc:  # a crashed attack is reported, never shown as a defence
            observed, expected, mandate_id, error = "error", "", None, f"{type(exc).__name__}: {exc}"
        ledger = lab.ledger(mandate_id) if mandate_id else None
        lab.client.close()
    return {
        "id": attack.id, "title": attack.title, "category": attack.category, "threat": attack.threat,
        "defence": attack.defence, "expected": expected, "observed": observed,
        "held": error is None and observed == expected, "error": error, "ran_at": iso(datetime.now(HKT)),
        "sandbox": {"isolated": True, "simulated_time": iso(lab.clock.now()),
                    "catalog": "placeholder fixture (not observed shop data)"},
        "ledger": ledger, "steps": lab.steps,
    }


def build_attack_lab_router() -> APIRouter:
    router = APIRouter(tags=["interface"])

    def _enabled(actor: Actor = Depends(current_actor)) -> Actor:
        require_role(actor, "user")
        if os.environ.get("MANDATE_ENABLE_DEMO_CHECKOUT") != "1":
            raise forbidden("The local security lab is disabled.")
        return actor

    @router.get("/demo/attacks", response_model=AttackList)
    def list_attacks(_: Actor = Depends(_enabled)):
        return {"attacks": [{k: getattr(a, k) for k in ("id", "title", "category", "threat", "defence")}
                            for a in ATTACKS]}

    @router.post("/demo/attacks/{attack_id}/runs", response_model=AttackResult)
    def run(attack_id: str, _: Actor = Depends(_enabled)):
        if attack_id not in _BY_ID:
            raise not_found("Attack")
        return run_attack(attack_id)

    return router
