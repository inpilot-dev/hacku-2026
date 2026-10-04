"""Shared setup and a thin client that speaks the wallet API shape.

``Api`` wraps any ``httpx.Client``: a live one pointed at a uvicorn server, or
FastAPI's ``TestClient`` (an httpx.Client subclass) for in-process scenarios.
The unsafe baseline exposes the same paths, so one client drives both.
"""

from __future__ import annotations

import statistics
import time
import uuid

import httpx

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}

# Grill decision: mum's weekly HK$800, per-order HK$300 (contracts example policy).
POLICY = {
    "currency": "HKD",
    "per_order_limit_minor": 30000,
    "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
    "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
    "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00",
    "approval_above_minor": None,
}


def basket(*items: tuple[str, int], merchant="demo_store_a", ctx="ctx_a_standard") -> dict:
    return {"merchant_id": merchant, "items": [{"product_id": p, "quantity": n} for p, n in items],
            "delivery_context_id": ctx}


# Placeholder catalog (fixtures/placeholder_catalog.json, NOT observed data), store A delivery HK$30 under HK$400.
PRESPEND = [basket(("p_a_rice", 2)),                    # HK$178 + HK$30 = HK$208.00
            basket(("p_a_rice", 1), ("p_a_milk", 2))]   # HK$162 + HK$30 = HK$192.00  -> HK$400 spent
RACE_BASKET = basket(("p_a_rice", 1), ("p_a_milk", 1), ("p_a_apples", 5))  # HK$270 + HK$30 = HK$300.00
NORMAL_BASKET = basket(("p_a_rice", 3))                                    # HK$267 + HK$30 = HK$297.00


def key() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


def txn() -> str:
    return str(uuid.uuid4())


class Api:
    def __init__(self, client: httpx.Client, prefix: str = "/api/v1"):
        self.c, self.p = client, prefix
        self.latency_ms: dict[str, list[float]] = {"authorize": [], "pay": []}

    def _post(self, path, headers, body, timer=None) -> dict:
        t0 = time.perf_counter()
        res = self.c.post(self.p + path, headers=headers, json=body)
        if timer:
            self.latency_ms[timer].append((time.perf_counter() - t0) * 1000)
        return {"http_status": res.status_code, **res.json()}

    def confirm(self, draft_id: str, policy: dict = POLICY) -> dict:
        out = self._post("/mandates/confirm", {**USER, **key()}, {"draft_id": draft_id, "policy": policy})
        assert out["http_status"] == 201, out
        return out

    def quote(self, body: dict) -> dict:
        return self._post("/quotes", AGENT, body)

    def authorize(self, mandate_id: str, quote_id: str, transaction_id: str | None = None, headers=None) -> dict:
        return self._post("/authorizations", headers or {**AGENT, **key()},
                          {"transaction_id": transaction_id or txn(), "mandate_id": mandate_id,
                           "quote_id": quote_id}, "authorize")

    def pay(self, approved: dict, headers=None, **override) -> dict:
        body = {"transaction_id": approved["transaction_id"], "quote_id": approved["claims"]["quote_id"],
                "authorization_token": approved["authorization_token"], **override}
        return self._post("/payments", headers or {**AGENT, **key()}, body, "pay")

    def revoke(self, mandate_id: str) -> dict:
        return self._post(f"/mandates/{mandate_id}/revoke", {**USER, **key()}, {"reason": "evaluation"})

    def cancel(self, reservation_id: str) -> dict:
        return self._post(f"/reservations/{reservation_id}/cancel", {**AGENT, **key()}, {})

    def receipt(self, transaction_id: str) -> dict:
        res = self.c.get(f"{self.p}/payments/{transaction_id}", headers=USER)
        return {"http_status": res.status_code, **res.json()}

    def week(self, mandate_id: str) -> dict:
        res = self.c.get(f"{self.p}/wallet/{mandate_id}", headers=USER)
        assert res.status_code == 200, res.text
        return next(b for b in res.json()["applicable_budgets"]
                    if b["mandate_id"] == mandate_id and b["period"] == "calendar_week")

    def buy(self, mandate_id: str, body: dict) -> dict:
        auth = self.authorize(mandate_id, self.quote(body)["id"])
        assert auth["status"] == "approved", auth
        paid = self.pay(auth)
        assert paid["status"] == "completed", paid
        return paid


def hkd(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}HK${abs(minor) // 100:,}.{abs(minor) % 100:02d}"


def pct(xs: list[float], p: int) -> float | None:
    if len(xs) < 2:
        return round(xs[0], 2) if xs else None
    return round(statistics.quantiles(xs, n=100, method="inclusive")[p - 1], 2)


def rate(n: int, d: int) -> dict:
    return {"count": n, "denominator": d, "rate": round(n / d, 4) if d else None}
