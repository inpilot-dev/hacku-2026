from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

import pytest

from catalog_capture import wellcome
from catalog_capture.capture import HKT, CaptureError, Run
from mandate.payments.catalog import Catalog

FIXTURE = Path(__file__).parent / "fixtures" / "wellcome_category_100020_excerpt.html"
CATEGORY_PAGE = FIXTURE.read_text(encoding="utf-8")


def product_page(price: str) -> str:
    offer = {"@type": "Product", "name": "x", "offers": {"@type": "Offer", "price": price, "priceCurrency": "HKD"}}
    return (f'<html><script type="application/ld+json">{json.dumps(offer)}</script>'
            f"<span>{wellcome.FREE_PICKUP_TEXT}</span></html>")


def fake_fetcher(prices: dict[str, str], pages: dict[str, str] | None = None):
    """Serve the fixture for every category, and product pages with the given JSON-LD prices by SKU."""
    def fetch(url: str) -> str:
        if pages and url in pages:
            return pages[url]
        if "/category/" in url:
            return CATEGORY_PAGE
        if "/i/" in url:
            return product_page(prices[url.rsplit("/i/", 1)[1].removesuffix(".html")])
        return "<html><title>Wellcome</title></html>"
    return fetch


TRUE_PRICES = {"101345302": "89.90", "101324109": "178.00", "101327889": "98.00"}


def make_run(tmp_path: Path, fetcher) -> Run:
    return Run(tmp_path, tmp_path / "data" / "catalog", fetcher=fetcher,
               now=datetime(2026, 10, 3, 0, 30, tzinfo=HKT), delay_s=0)


def test_parse_category_reads_current_price_not_struck_through_price():
    listings = {item.sku: item for item in wellcome.parse_category(CATEGORY_PAGE)}
    rice = listings["101345302"]
    assert rice.title == "Golden Elephant Premium Jasmine Rice 8KG"
    assert rice.price_minor == 8990
    assert rice.was_price_minor == 11290
    assert rice.available
    assert rice.product_url.startswith("https://www.wellcome.com.hk/en/wellcome/p/")
    assert listings["101327889"].was_price_minor is None
    assert wellcome.category_title(CATEGORY_PAGE) == "Rice, Oil & Noodles"


@pytest.mark.parametrize("dollars,cents,expected", [("89", "90", 8990), ("1,234", None, 123400),
                                                     ("112.90", None, 11290), ("5", "9", 590)])
def test_minor_units(dollars, cents, expected):
    assert wellcome._minor(dollars, cents) == expected


def test_json_ld_price():
    assert wellcome.json_ld_price_minor(product_page("89.90")) == 8990
    assert wellcome.json_ld_price_minor("<html>no data</html>") is None


def test_alcohol_words():
    assert wellcome.looks_alcoholic("Asahi Super Dry Beer 6x350ml")
    assert not wellcome.looks_alcoholic("Golden Elephant Premium Jasmine Rice 8KG")


def test_build_and_write_produces_a_catalog_the_wallet_can_price(tmp_path):
    run = make_run(tmp_path, fake_fetcher(TRUE_PRICES))
    catalog = run.build({"100020": "pantry"})
    target = run.write(catalog)

    loaded = Catalog.load(target)
    quote = loaded.price("wellcome", [{"product_id": "wellcome_101345302", "quantity": 2}], "ctx_wellcome_click_collect")
    assert quote["subtotal_minor"] == 17980
    assert quote["total_minor"] == 17980
    assert [c["amount_minor"] for c in quote["charges"]] == [0]
    assert quote["data_mode"] == "observed_snapshot"

    evidence = {e["id"]: e for e in catalog["evidence"]}
    for product in catalog["products"]:
        kinds = {evidence[i]["kind"] for i in product["evidence_ids"]}
        assert kinds == {"product_price", "category"}
    for item in evidence.values():
        assert item["source_url"].startswith("https://www.wellcome.com.hk/")
        assert item["conditions"] and "placeholder" not in item["conditions"].lower()
        assert datetime.fromisoformat(item["observed_at"]).utcoffset() is not None
        with gzip.open(tmp_path / item["capture_path"], "rt", encoding="utf-8") as fh:
            assert fh.read()
    assert {e["kind"] for e in evidence.values()} == {"merchant_identity", "category", "product_price", "delivery_fee"}


def test_listing_in_two_differently_mapped_categories_becomes_conflicting(tmp_path):
    run = make_run(tmp_path, fake_fetcher(TRUE_PRICES))
    catalog = run.build({"100020": "pantry", "100001": "alcohol"})
    rice = next(p for p in catalog["products"] if p["id"] == "wellcome_101345302")
    assert rice["category"] == "pantry"
    assert rice["category_status"] == "conflicting"
    assert len([p for p in catalog["products"] if p["id"] == "wellcome_101345302"]) == 1


def test_every_category_gets_a_price_cross_check(tmp_path):
    # A second category page with different SKUs, so its listings are not duplicates.
    alcohol_page = CATEGORY_PAGE.replace("/i/1013", "/i/9013").replace("/i/1018", "/i/9018")
    prices = {**TRUE_PRICES, "901345302": "89.90"}
    run = make_run(tmp_path, fake_fetcher(prices, {wellcome.category_url("100001"): alcohol_page}))
    catalog = run.build({"100020": "pantry", "100001": "alcohol"})
    checked = sorted(name for name in run.pages if name.startswith("product-"))
    assert checked == ["product-101345302", "product-901345302"]
    assert {p["category"] for p in catalog["products"]} == {"pantry", "alcohol"}


def test_price_mismatch_aborts_and_writes_nothing(tmp_path):
    run = make_run(tmp_path, fake_fetcher({**TRUE_PRICES, "101345302": "99.90"}))
    with pytest.raises(CaptureError, match="101345302"):
        run.build({"100020": "pantry"})
    assert not (tmp_path / "data").exists()


def test_empty_category_aborts(tmp_path):
    empty = '<html><div class="category-big-title">Empty</div></html>'
    run = make_run(tmp_path, fake_fetcher(TRUE_PRICES, {wellcome.category_url("100020"): empty}))
    with pytest.raises(CaptureError, match="No priced listings"):
        run.build({"100020": "pantry"})


def test_market_place_capture_uses_its_own_site_and_ids(tmp_path):
    fetched: list[str] = []
    inner = fake_fetcher(TRUE_PRICES)

    def fetch(url):
        fetched.append(url)
        return inner(url)

    run = Run(tmp_path, tmp_path / "data" / "catalog", fetcher=fetch, now=datetime(2026, 10, 3, 0, 30, tzinfo=HKT),
              delay_s=0, prefix="marketplace")
    catalog = run.build({"100020": "pantry"}, shop=wellcome.MARKETPLACE)
    assert all(url.startswith("https://www.marketplacehk.com/") for url in fetched)
    assert {p["merchant_id"] for p in catalog["products"]} == {"marketplace"}
    assert "marketplace_101345302" in {p["id"] for p in catalog["products"]}
    assert list(catalog["merchants"]) == ["marketplace"]
    assert catalog["delivery_contexts"][0]["id"] == "ctx_marketplace_click_collect"
    assert all(e["source_url"].startswith("https://www.marketplacehk.com/") for e in catalog["evidence"])
    assert all(e["capture_path"].startswith("data/catalog/evidence/marketplace-") for e in catalog["evidence"])


def test_combined_catalog_quotes_both_shops_and_refuses_duplicate_ids(tmp_path):
    from catalog_capture.combine import CombineError, combine

    def build(shop):
        run = Run(tmp_path, tmp_path / "data" / "catalog", fetcher=fake_fetcher(TRUE_PRICES),
                  now=datetime(2026, 10, 3, 0, 30, tzinfo=HKT), delay_s=0, prefix=shop.merchant_id)
        return run.build({"100020": "pantry"}, shop=shop)

    w, m = build(wellcome.WELLCOME), build(wellcome.MARKETPLACE)
    joined = Catalog(combine([w, m]))
    for shop in (wellcome.WELLCOME, wellcome.MARKETPLACE):
        quote = joined.price(shop.merchant_id, [{"product_id": f"{shop.merchant_id}_101345302", "quantity": 1}],
                             shop.pickup_context_id)
        assert quote["merchant_id"] == shop.merchant_id and quote["total_minor"] == 8990
    with pytest.raises(CombineError, match="wellcome"):
        combine([w, w])
