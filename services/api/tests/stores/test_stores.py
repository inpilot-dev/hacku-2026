from __future__ import annotations

import json
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts
from mandate.stores.carts import CartSync, parse_cart
from mandate.stores.connections import LOGIN_TIMEOUT_S, StoreConnections
from mandate.stores.registry import STORES
from mandate.stores.routes import build_store_router
from mandate.stores.steel import StoreBrowserError
from mandate.stores.stream import VIEWPORT, allowed_url, input_commands
from starlette.websockets import WebSocketDisconnect

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}
MILK, PEAR, WAFER = 101355093, 101373041, 101366861


def product(sku, title, price):
    return {"id": f"wellcome_{sku}", "merchant_id": "wellcome", "title": title, "description": title,
            "category": "pantry", "category_status": "curated", "unit_label": "pack", "unit_price_minor": price,
            "currency": "HKD", "available": True, "evidence_ids": ["ev"]}


CATALOG = {
    "merchants": {"wellcome": {"revision": "r1", "data_mode": "observed_snapshot", "evidence_ids": ["ev"]},
                  "other": {"revision": "r1", "data_mode": "observed_snapshot", "evidence_ids": ["ev"]}},
    "products": [product(MILK, "Fresh Milk 1L", 3800), product(PEAR, "Ya Pear 1EA", 400),
                 {**product(1, "Other shop rice", 9000), "id": "other_1", "merchant_id": "other"}],
    "delivery_contexts": [{"id": ctx, "merchant_id": m, "fee_rules": [
        {"label": "Pickup", "min_subtotal_minor": 0, "max_subtotal_minor": None, "amount_minor": 0,
         "evidence_ids": ["ev"]}]} for ctx, m in (("ctx_w", "wellcome"), ("ctx_o", "other"))],
    "evidence": [{"id": "ev", "source_url": "https://shop.example.com/", "observed_at": "2026-10-03T00:00:00+08:00",
                  "kind": "product_price", "capture_path": "x.html.gz", "conditions": "Guest session."}],
}
POLICY = {"currency": "HKD", "per_order_limit_minor": 30000,
          "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
          "allowed_merchant_ids": ["wellcome"], "blocked_categories": ["alcohol"],
          "expires_at": "2026-10-31T23:59:59+08:00", "approval_above_minor": None}


class FakeShop:
    """A superweb cart as observed: addToCart applies count - beforeCount; count 0 removes the line."""

    def __init__(self):
        self.lines: dict[int, dict] = {}
        self.prices = {MILK: 3800, PEAR: 450, WAFER: 2500}
        self.signed_in = True
        self.writes: list[tuple[int, int, int]] = []
        self.fail_write_at: int | None = None  # the write whose answer is lost (applied, then error)
        self.refuse: set[int] = set()

    def cart(self):
        wares = [{"skuId": sku, "count": l["count"], "unitSinglePrice": self.prices[sku], "wareName": f"sku {sku}",
                  "itemSubtotalPrice": self.prices[sku] * l["count"], "checked": l["checked"]}
                 for sku, l in self.lines.items()]
        return {"code": "0000", "data": {"storeGroupList": [{"storeList": [{"itemGroupList": [
            {"itemList": [{"wareList": wares}]}]}]}]}}

    def add(self, sku, before, count):
        self.writes.append((sku, before, count))
        if sku in self.refuse:
            return {"code": "E100"}
        new = self.lines.get(sku, {"count": 0})["count"] + count - before
        if new <= 0:
            self.lines.pop(sku, None)
        else:
            self.lines[sku] = {"count": new, "checked": 1}
        if self.fail_write_at == len(self.writes):
            raise StoreBrowserError("The shop did not answer.")
        return {"code": "0000"}


class FakeCart:
    def __init__(self, shop):
        self.shop = shop

    def signed_in(self):
        return self.shop.signed_in

    def cart(self):
        return self.shop.cart()

    def add(self, sku, before, count):
        return self.shop.add(sku, before, count)


class FakeBrowser:
    def __init__(self):
        self.shop = FakeShop()
        self.login_done = False
        self.open_windows: list[str] = []
        self.sessions: list[dict] = []

    def begin_login(self, store):
        window = f"win-{len(self.open_windows)}"
        self.open_windows.append(window)
        return window

    def poll_login(self, window, store):
        if not self.login_done:
            return None
        return {"cookies": [{"name": "LOGIN_FLAG", "value": "secret", "domain": store.domain}],
                "local_storage": {"commData": "{}", "accountInfo": "personal"}}

    def end_login(self, window):
        self.open_windows.remove(window)

    @contextmanager
    def session(self, store, state):
        self.sessions.append(state)
        yield FakeCart(self.shop)


async def fake_relay(window, store, send, receive, finished):
    """Echoes what the real relay would turn each client message into, then reports the final status."""
    await send({"type": "viewport", **VIEWPORT, "window": window})
    while (msg := await receive()) is not None:
        if msg == {"type": "done?"}:
            await send({"type": "status", "status": await finished()})
            return
        await send({"type": "commands", "commands": [m for m, _ in input_commands(msg)]})


class Ticks:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(CATALOG))
    monkeypatch.setenv("MANDATE_CATALOG_PATH", str(path))
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path / "wallet", clock=FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT)),
                             draft_lookup=drafts)
    browser, ticks = FakeBrowser(), Ticks()
    stores = StoreConnections(browser, wallet.clock.now, tmp_path / "stores", monotonic=ticks)
    app.include_router(build_store_router(stores, CartSync(wallet, stores, browser), relay=fake_relay),
                       prefix="/api/v1")
    client = TestClient(app)

    def confirm(policy=POLICY):
        draft = f"draft_{uuid.uuid4().hex}"
        drafts.register(draft, owner_id="user_demo", delegatee_id="agent_student",
                        expires_at="2026-10-31T23:59:59+08:00")
        res = client.post("/api/v1/mandates/confirm", headers={**USER, "Idempotency-Key": str(uuid.uuid4())},
                          json={"draft_id": draft, "policy": policy})
        assert res.status_code == 201, res.text
        return res.json()["id"]

    def quote(items, merchant="wellcome", ctx="ctx_w"):
        res = client.post("/api/v1/quotes", headers=USER, json={
            "merchant_id": merchant, "delivery_context_id": ctx,
            "items": [{"product_id": f"{merchant}_{sku}", "quantity": q} for sku, q in items]})
        assert res.status_code == 201, res.text
        return res.json()["id"]

    def connect():
        assert client.post("/api/v1/stores/wellcome/connect", headers=USER).status_code == 202
        browser.login_done = True
        assert client.get("/api/v1/stores/wellcome", headers=USER).json()["status"] == "connected"

    return client, browser, tmp_path, ticks, confirm, quote, connect


def sync(client, mandate, quote):
    res = client.post("/api/v1/carts/sync", headers=USER, json={"mandate_id": mandate, "quote_id": quote})
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------------ connections


def test_sign_in_flow_saves_session_privately_and_never_returns_it(env):
    client, browser, tmp, _, *_ = env
    assert client.get("/api/v1/stores", headers=USER).json()["stores"][0]["status"] == "not_connected"
    started = client.post("/api/v1/stores/wellcome/connect", headers=USER).json()
    assert started["status"] == "awaiting_login" and "viewer_url" not in started
    assert client.get("/api/v1/stores/wellcome", headers=USER).json()["status"] == "awaiting_login"

    browser.login_done = True
    done = client.get("/api/v1/stores/wellcome", headers=USER)
    assert done.json()["status"] == "connected" and done.json()["connected_at"]
    assert "secret" not in done.text and "personal" not in done.text
    assert browser.open_windows == []  # the sign-in window is closed once the session is saved

    saved = tmp / "stores" / "user_demo" / "wellcome.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700

    assert client.delete("/api/v1/stores/wellcome/connection", headers=USER).json()["status"] == "not_connected"
    assert not saved.exists()


def test_sign_in_times_out_and_closes_the_window(env):
    client, browser, _, ticks, *_ = env
    client.post("/api/v1/stores/wellcome/connect", headers=USER)
    ticks.t = LOGIN_TIMEOUT_S + 1
    assert client.get("/api/v1/stores/wellcome", headers=USER).json()["status"] == "not_connected"
    assert browser.open_windows == []


def test_agents_and_unknown_stores_are_refused(env):
    client, *_ = env
    assert client.get("/api/v1/stores", headers=AGENT).status_code == 403
    assert client.post("/api/v1/stores/parknshop/connect", headers=USER).status_code == 404
    assert client.post("/api/v1/carts/sync", headers=AGENT, json={"mandate_id": "m", "quote_id": "q"}).status_code == 403


def ticket(client):
    res = client.post("/api/v1/stores/wellcome/login/ticket", headers=USER)
    assert res.status_code == 200, res.text
    return res.json()["ticket"]


def test_stream_needs_a_pending_sign_in_and_a_single_use_ticket(env):
    client, browser, *_ = env
    assert client.post("/api/v1/stores/wellcome/login/ticket", headers=USER).json()["error"]["code"] == "LOGIN_NOT_STARTED"
    assert client.post("/api/v1/stores/wellcome/login/ticket", headers=AGENT).status_code == 403
    client.post("/api/v1/stores/wellcome/connect", headers=USER)
    good = ticket(client)
    for bad in ("", "made-up"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/v1/stores/wellcome/login/stream?ticket={bad}") as ws:
                ws.receive_json()

    with client.websocket_connect(f"/api/v1/stores/wellcome/login/stream?ticket={good}") as ws:
        assert ws.receive_json()["window"] == "win-0"  # this user's own sign-in window
        ws.send_json({"type": "text", "text": "98765432"})
        assert ws.receive_json()["commands"] == ["Input.insertText"]
        ws.send_json({"type": "eval", "expression": "document.cookie"})
        assert ws.receive_json()["commands"] == []  # nothing outside the input set reaches the browser
        browser.login_done = True
        ws.send_json({"type": "done?"})
        assert ws.receive_json() == {"type": "status", "status": "connected"}

    with pytest.raises(WebSocketDisconnect):  # the ticket was used
        with client.websocket_connect(f"/api/v1/stores/wellcome/login/stream?ticket={good}") as ws:
            ws.receive_json()


def test_input_commands_are_validated_and_clamped():
    down = input_commands({"type": "down", "x": 10, "y": 99999})
    assert [p["type"] for _, p in down] == ["mouseMoved", "mousePressed"] and down[1][1]["y"] == 4096
    assert input_commands({"type": "down", "x": "10", "y": 5}) == []
    assert [p["type"] for _, p in input_commands({"type": "key", "key": "Enter"})] == ["keyDown", "keyUp"]
    assert input_commands({"type": "key", "key": "F12"}) == []
    assert input_commands({"type": "text", "text": "x" * 65}) == []
    assert input_commands({"type": "text", "text": "\x1b[A"}) == []
    assert input_commands("down") == []


def test_navigation_is_kept_on_the_sign_in_and_shop_sites():
    store = STORES["wellcome"]
    assert allowed_url("https://www.yuurewards.com/en/super/login-info", store)
    assert allowed_url("https://www.wellcome.com.hk/en/cart", store)
    assert allowed_url("about:blank", store)
    assert not allowed_url("https://evil.example.com/", store)
    assert not allowed_url("https://wellcome.com.hk.evil.example.com/", store)
    assert not allowed_url("http://www.wellcome.com.hk/", store)
    assert not allowed_url("file:///etc/passwd", store)


# ------------------------------------------------------------------- cart sync


def test_sync_sets_quoted_quantities_and_flags_shop_price(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    result = sync(client, confirm(), quote([(MILK, 2), (PEAR, 1)]))
    assert browser.shop.lines == {MILK: {"count": 2, "checked": 1}, PEAR: {"count": 1, "checked": 1}}
    lines = {l["sku"]: l for l in result["lines"]}
    assert lines[str(MILK)]["status"] == "ok"
    # The shop charges HK$4.50 for the pear the snapshot priced at HK$4.00: reported, never papered over.
    assert lines[str(PEAR)]["status"] == "price_changed" and lines[str(PEAR)]["cart_unit_price_minor"] == 450
    assert result["status"] == "mismatch" and not result["checkout_ready"]
    assert result["cart_subtotal_minor"] == 2 * 3800 + 450 and result["quote_subtotal_minor"] == 2 * 3800 + 400
    assert "never checks out" in result["message"]


def test_repeat_sync_does_not_double(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    browser.shop.lines[MILK] = {"count": 1, "checked": 1}
    mandate, q = confirm(), quote([(MILK, 2)])
    first = sync(client, mandate, q)
    second = sync(client, mandate, q)
    assert browser.shop.lines == {MILK: {"count": 2, "checked": 1}}
    assert browser.shop.writes == [(MILK, 1, 2)]  # the second sync found nothing to change
    assert first["checkout_ready"] and second["checkout_ready"] and second["status"] == "synced"


def test_users_own_items_are_never_reduced(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    browser.shop.lines[MILK] = {"count": 3, "checked": 1}
    result = sync(client, confirm(), quote([(MILK, 1)]))
    assert browser.shop.lines[MILK]["count"] == 3 and browser.shop.writes == []
    assert result["lines"][0]["status"] == "quantity_mismatch" and result["lines"][0]["cart_quantity"] == 3
    assert not result["checkout_ready"] and "left them as they were" in result["message"]


def test_other_selected_items_block_checkout_and_are_left_alone(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    browser.shop.lines[WAFER] = {"count": 2, "checked": 1}
    result = sync(client, confirm(), quote([(MILK, 1)]))
    assert browser.shop.lines[WAFER] == {"count": 2, "checked": 1}
    assert result["other_items"] == [{"sku": str(WAFER), "title": f"sku {WAFER}", "quantity": 2,
                                      "unit_price_minor": 2500, "checked": True}]
    assert not result["checkout_ready"] and "other selected item" in result["message"]


def test_lost_write_answer_is_not_resent(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    browser.shop.fail_write_at = 1
    result = sync(client, confirm(), quote([(MILK, 2), (PEAR, 1)]))
    assert browser.shop.writes == [(MILK, 0, 2)]  # applied by the shop, answer lost; not retried, then stop
    assert result["status"] == "partial" and not result["checkout_ready"]
    assert {l["sku"]: l["status"] for l in result["lines"]} == {str(MILK): "ok", str(PEAR): "missing"}


def test_refused_line_is_reported(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    browser.shop.refuse = {MILK}
    result = sync(client, confirm(), quote([(MILK, 1)]))
    assert result["status"] == "partial" and "refused" in result["message"]


def test_not_connected_and_signed_out_touch_nothing(env):
    client, browser, _, _, confirm, quote, connect = env
    mandate, q = confirm(), quote([(MILK, 1)])
    assert sync(client, mandate, q)["status"] == "not_connected"
    connect()
    browser.shop.signed_in = False
    assert sync(client, mandate, q)["status"] == "session_expired"
    assert browser.shop.writes == []
    assert client.get("/api/v1/stores/wellcome", headers=USER).json()["status"] == "expired"
    assert sync(client, mandate, q)["status"] == "not_connected"  # expired sessions are not reused


def test_mandate_gates_which_carts_are_touched(env):
    client, browser, _, _, confirm, quote, connect = env
    connect()
    other = client.post("/api/v1/carts/sync", headers=USER,
                        json={"mandate_id": confirm(), "quote_id": quote([(1, 1)], "other", "ctx_o")})
    assert other.status_code == 409 and other.json()["error"]["code"] == "MERCHANT_NOT_ALLOWED"
    mandate = confirm()
    client.post(f"/api/v1/mandates/{mandate}/revoke", headers={**USER, "Idempotency-Key": "r1"}, json={})
    revoked = client.post("/api/v1/carts/sync", headers=USER, json={"mandate_id": mandate, "quote_id": quote([(MILK, 1)])})
    assert revoked.status_code == 409 and revoked.json()["error"]["code"] == "MANDATE_NOT_ACTIVE"
    assert browser.shop.writes == []


# ------------------------------------------------------------------- parsing


def test_parse_cart_reads_lines_only():
    body = {"code": "0000", "data": {"userId": 1, "storeGroupList": [{"storeList": [{"itemGroupList": [
        {"itemList": [{"wareList": [{"skuId": 5, "count": 2, "unitSinglePrice": 450, "itemSubtotalPrice": 800,
                                     "wareName": "Pear", "checked": 0},
                                    {"skuId": None, "count": 1}]}]}]}]}]}}
    lines = parse_cart(body)
    assert list(lines) == [5] and lines[5].quantity == 2 and lines[5].unit_price_minor == 450 and not lines[5].checked
    assert lines[5].line_total_minor == 800  # a 2-for offer: the store's line total, not 2 x 450


def test_market_place_is_its_own_store_on_the_same_platform():
    store = STORES["marketplace"]
    assert store.sku("marketplace_101365694") == 101365694 and store.sku("wellcome_101365694") is None
    assert store.domain_flag == "marketplace" and store.vender_id == 5
    assert "domain=marketplacehk.com" in store.login_url()
    assert store.login_url().startswith("https://www.yuurewards.com/en/super/login-info?callbackUrl=https://www.marketplacehk.com/api/login/callback")
    assert allowed_url("https://www.marketplacehk.com/en/cart", store)
    assert not allowed_url("https://www.wellcome.com.hk/en/cart", store)  # each sign-in stays on its own shop


def test_stores_list_includes_market_place(env):
    client, *_ = env
    stores = client.get("/api/v1/stores", headers=USER).json()["stores"]
    assert [(s["store_id"], s["name"], s["status"]) for s in stores] == [
        ("wellcome", "Wellcome", "not_connected"), ("marketplace", "Market Place", "not_connected")]


def test_wellcome_store_maps_skus_and_login_url():
    store = STORES["wellcome"]
    assert store.sku("wellcome_101355093") == 101355093
    assert store.sku("wellcome_abc") is None and store.sku("other_1") is None
    assert store.login_url().startswith("https://www.yuurewards.com/en/super/login-info?callbackUrl=https://www.wellcome.com.hk/api/login/callback")
    assert "venderId=5" in store.login_url()
