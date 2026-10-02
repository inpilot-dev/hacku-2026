from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mandate.agent.catalog_routes import build_catalog_router
from mandate.payments.dev_app import create_app

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}

OBSERVED = {
    "merchants": {"shop_x": {"revision": "r1", "data_mode": "observed_snapshot", "evidence_ids": ["ev_id"]}},
    "products": [{
        "id": "shop_x_rice", "merchant_id": "shop_x", "title": "Rice 5KG", "description": "Rice 5KG",
        "category": "pantry", "category_status": "curated", "unit_label": "pack", "unit_price_minor": 7290,
        "currency": "HKD", "available": True, "evidence_ids": ["ev_price"],
    }],
    "delivery_contexts": [{"id": "ctx_x", "merchant_id": "shop_x", "fee_rules": [
        {"label": "Pickup", "min_subtotal_minor": 5001, "max_subtotal_minor": None, "amount_minor": 0,
         "evidence_ids": ["ev_fee"]}]}],
    "evidence": [
        {"id": ev, "source_url": "https://shop.example.com/", "observed_at": "2026-10-03T00:30:00+08:00",
         "kind": kind, "capture_path": "data/catalog/evidence/x.html.gz", "conditions": "Guest session."}
        for ev, kind in (("ev_id", "merchant_identity"), ("ev_price", "product_price"), ("ev_fee", "delivery_fee"),
                         ("ev_unused", "product_price"))
    ],
}


def client(tmp_path) -> TestClient:
    app, wallet = create_app(tmp_path)
    app.include_router(build_catalog_router(wallet), prefix="/api/v1")
    return TestClient(app)


@pytest.mark.parametrize("headers", [USER, AGENT])
def test_user_and_agent_can_list_the_catalog(tmp_path, headers):
    res = client(tmp_path).get("/api/v1/catalog", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"products", "evidence"}
    evidence_ids = {e["id"] for e in body["evidence"]}
    for product in body["products"]:
        assert set(product["evidence_ids"]) <= evidence_ids


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
def test_catalog_requires_a_valid_token(tmp_path, headers):
    res = client(tmp_path).get("/api/v1/catalog", headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"]


def test_merchant_filter_and_unknown_merchant(tmp_path):
    c = client(tmp_path)
    body = c.get("/api/v1/catalog", params={"merchant_id": "demo_store_a"}, headers=USER).json()
    assert body["products"] and {p["merchant_id"] for p in body["products"]} == {"demo_store_a"}
    res = c.get("/api/v1/catalog", params={"merchant_id": "no_such_store"}, headers=USER)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_observed_catalog_lists_cited_evidence_and_matches_the_quote(tmp_path, monkeypatch):
    path = tmp_path / "observed.json"
    path.write_text(json.dumps(OBSERVED))
    monkeypatch.setenv("MANDATE_CATALOG_PATH", str(path))
    c = client(tmp_path)

    body = c.get("/api/v1/catalog", params={"merchant_id": "shop_x"}, headers=USER).json()
    assert [p["unit_price_minor"] for p in body["products"]] == [7290]
    # Merchant, price and fee evidence are listed; evidence nothing cites is not.
    assert {e["id"] for e in body["evidence"]} == {"ev_id", "ev_price", "ev_fee"}

    quote = c.post("/api/v1/quotes", headers=USER, json={
        "merchant_id": "shop_x", "items": [{"product_id": "shop_x_rice", "quantity": 1}],
        "delivery_context_id": "ctx_x"})
    assert quote.status_code == 201, quote.text
    assert quote.json()["items"][0]["unit_price_minor"] == 7290
