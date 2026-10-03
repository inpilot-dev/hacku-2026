"""A basket whose subtotal no observed fee rule covers is refused on the server, not only in the UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from mandate.payments.catalog import Catalog, CatalogError

from .conftest import AGENT, RICE_X3

WELLCOME = Path(__file__).resolve().parents[4] / "data" / "catalog" / "wellcome.json"
PICKUP = "ctx_wellcome_click_collect"
HK5 = "wellcome_101322566"  # HK$5.00 in the captured snapshot


def wellcome_basket(quantity: int) -> list[dict]:
    return [{"product_id": HK5, "quantity": quantity}]


@pytest.fixture(scope="module")
def wellcome() -> Catalog:
    return Catalog.load(WELLCOME)


def test_wellcome_pickup_at_or_below_hk50_is_refused(wellcome):
    assert wellcome.listing("wellcome")["products"]  # snapshot still carries the product used below
    with pytest.raises(CatalogError) as exc:
        wellcome.price("wellcome", wellcome_basket(10), PICKUP)  # exactly HK$50.00
    assert exc.value.kind == "invalid"
    assert exc.value.details["reason_code"] == "SUBTOTAL_NOT_SUPPORTED"
    assert exc.value.details["subtotal_minor"] == 5000
    assert exc.value.details["supported_ranges"] == [{"min_subtotal_minor": 5001, "max_subtotal_minor": None}]


def test_wellcome_pickup_above_hk50_is_free(wellcome):
    priced = wellcome.price("wellcome", wellcome_basket(11), PICKUP)
    assert priced["subtotal_minor"] == 5500
    assert [c["amount_minor"] for c in priced["charges"]] == [0]
    assert priced["total_minor"] == 5500


GAPPED = [{"label": "Delivery over HK$300", "min_subtotal_minor": 30000, "max_subtotal_minor": None,
           "amount_minor": 0, "evidence_ids": ["ev_fee_a_std"]}]


def test_quote_api_returns_422_for_uncovered_subtotal(h):
    h.wallet.catalog = h.wallet.catalog.with_changes(fee_rules={"ctx_a_standard": GAPPED})
    res = h.client.post("/api/v1/quotes", headers=AGENT, json=RICE_X3)  # HK$267 subtotal
    assert res.status_code == 422, res.text
    error = res.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert error["details"]["reason_code"] == "SUBTOTAL_NOT_SUPPORTED"
    assert error["details"]["subtotal_minor"] == 26700


def test_payment_is_refused_when_fee_coverage_is_withdrawn(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.wallet.catalog = h.wallet.catalog.with_changes(fee_rules={"ctx_a_standard": GAPPED})
    paid = h.pay(auth).json()
    assert paid["violations"][0]["code"] == "QUOTE_CHANGED"
