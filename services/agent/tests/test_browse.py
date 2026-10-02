from __future__ import annotations

import gzip
import json
from datetime import datetime

import pytest

from catalog_capture import product_data as pd
from catalog_capture.browse import STORES, BrowseRun
from catalog_capture.capture import HKT, CaptureError
from mandate.payments.catalog import Catalog

ORIGIN = "https://www.wellcome.com.hk"
FEE_TEXT = STORES["wellcome"].fees[0].page_text


def ld(name, price, currency="HKD", **offer):
    return json.dumps({"@context": "https://schema.org", "@type": "Product", "name": name,
                       "offers": {"@type": "Offer", "price": price, "priceCurrency": currency, **offer}})


# --- product_data ---------------------------------------------------------

@pytest.mark.parametrize("amount,expected", [("89.90", 8990), (98, 9800), ("1,234.5", 123450), ("5.9", 590)])
def test_to_minor(amount, expected):
    assert pd.to_minor(amount) == expected


@pytest.mark.parametrize("bad", ["-1", "1.234", "abc", "", 0.1 + 0.2])
def test_to_minor_rejects_non_amounts(bad):
    with pytest.raises(ValueError):
        pd.to_minor(bad)


def test_product_offer_accepts_object_list_and_graph_forms():
    assert pd.product_offer([ld("Rice", "89.90")]).price_minor == 8990
    as_list = json.dumps([{"@type": "BreadcrumbList"}, json.loads(ld("Rice", 89.9))])
    assert pd.product_offer([as_list]).price_minor == 8990
    graph = json.dumps({"@graph": [{"@type": ["Product"], "name": "Rice",
                                    "offers": [{"@type": "Offer", "price": "89.90", "priceCurrency": "HKD"}]}]})
    assert pd.product_offer([graph]).name == "Rice"


def test_product_offer_rejects_ambiguous_or_foreign_prices():
    aggregate = json.dumps({"@type": "Product", "name": "Rice",
                            "offers": {"@type": "AggregateOffer", "lowPrice": "80", "highPrice": "99",
                                       "priceCurrency": "HKD"}})
    assert pd.product_offer([aggregate]) is None
    assert pd.product_offer([ld("Rice", "89.90", currency="USD")]) is None
    two_offers = json.dumps({"@type": "Product", "name": "Rice", "offers": [
        {"@type": "Offer", "price": "89.90", "priceCurrency": "HKD"},
        {"@type": "Offer", "price": "79.90", "priceCurrency": "HKD"}]})
    assert pd.product_offer([two_offers]) is None
    assert pd.product_offer([ld("Rice", "89.90"), ld("Other", "10.00")]) is None
    assert pd.product_offer(["not json", "{}"]) is None


def test_availability():
    assert pd.availability_known(pd.Offer("x", 1, "https://schema.org/InStock")) is True
    assert pd.availability_known(pd.Offer("x", 1, "OutOfStock")) is False
    assert pd.availability_known(pd.Offer("x", 1, None)) is None


def test_price_visible_ignores_split_whitespace():
    assert pd.price_visible("Now $89 .90 was $112.90", 8990)
    assert pd.price_visible("Price $178", 17800)
    assert pd.price_visible("$1,234.50", 123450)
    assert not pd.price_visible("Now $99.90", 8990)
    assert not pd.price_visible("$1780", 17800)


def test_matching_links_same_site_all_words_dedup():
    links = [{"href": f"{ORIGIN}/p/a#x", "text": "Jasmine Rice 8KG"},
             {"href": f"{ORIGIN}/p/a", "text": "Jasmine Rice 8KG"},
             {"href": "https://elsewhere.com/rice", "text": "Rice"},
             {"href": f"{ORIGIN}/p/b", "text": "Rice Noodles"}]
    assert [l["href"] for l in pd.matching_links(links, "jasmine rice", ORIGIN)] == [f"{ORIGIN}/p/a"]
    assert len(pd.matching_links(links, "rice", ORIGIN)) == 2


def test_alcohol_guard_catches_rice_wine():
    assert pd.looks_alcoholic("Shaoxing Rice Wine 600ML")
    assert pd.looks_alcoholic("紹興酒 600ML")
    assert not pd.looks_alcoholic("Jasmine Rice 8KG")


# --- BrowseRun with a fake browser session --------------------------------

PAGES = {
    f"{ORIGIN}/p/rice8": {"ld": [ld("Jasmine Rice 8KG", "89.90")], "text": "Jasmine Rice 8KG $89 .90",
                          "extra": FEE_TEXT},
    f"{ORIGIN}/p/wine": {"ld": [ld("Shaoxing Rice Wine 600ML", "39.00")], "text": "Shaoxing Rice Wine $39.00"},
    f"{ORIGIN}/p/hidden": {"ld": [ld("Brown Rice 5KG", "50.00")], "text": "Brown Rice 5KG $55.00"},
    f"{ORIGIN}/c/rice": {"ld": [], "text": "Rice category"},
}
LINKS = [{"href": f"{ORIGIN}/c/rice", "text": "Rice"},
         {"href": f"{ORIGIN}/p/rice8", "text": "Jasmine Rice 8KG"},
         {"href": f"{ORIGIN}/p/wine", "text": "Shaoxing Rice Wine 600ML"},
         {"href": f"{ORIGIN}/p/hidden", "text": "Brown Rice 5KG"}]


class FakeSession:
    status = "done"
    opened: list[str] = []

    def __init__(self, start_url, goal):
        self.url = start_url
        self.goal = goal

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def run(self):
        self.url = f"{ORIGIN}/listing"
        return self.status

    def snapshot(self):
        return {"url": self.url, "html": f"<html>{self.url}</html>", "text": "", "ld": []}

    def links(self):
        return LINKS

    def open(self, url):
        FakeSession.opened.append(url)
        page = PAGES[url]
        self.url = url
        return {"url": url, "html": f"<html>{page['text']} {page.get('extra', '')}</html>", "text": page["text"],
                "ld": page["ld"]}


def make_run(tmp_path, factory=FakeSession, store="wellcome"):
    FakeSession.opened = []
    return BrowseRun(tmp_path, tmp_path / "data" / "catalog", store, session_factory=factory,
                     now=datetime(2026, 10, 3, 1, 0, tzinfo=HKT))


def test_browse_keeps_only_verifiable_products_and_writes_quotable_catalog(tmp_path):
    run = make_run(tmp_path)
    catalog = run.build([("rice", "pantry")])
    target = run.write(catalog, "browse-wellcome.json")

    by_title = {p["title"]: p for p in catalog["products"]}
    assert set(by_title) == {"Jasmine Rice 8KG", "Shaoxing Rice Wine 600ML"}  # hidden price and category page dropped
    assert by_title["Jasmine Rice 8KG"]["category_status"] == "curated"
    assert by_title["Shaoxing Rice Wine 600ML"]["category_status"] == "conflicting"
    assert any("price not in visible text" in line for line in run.report)

    evidence = {e["id"]: e for e in catalog["evidence"]}
    price = evidence[by_title["Jasmine Rice 8KG"]["evidence_ids"][0]]
    assert price["source_url"] == f"{ORIGIN}/p/rice8"
    assert "Availability is not published" in price["conditions"]
    category = evidence[by_title["Jasmine Rice 8KG"]["evidence_ids"][1]]
    assert "Operator mapping for search term 'rice'" in category["conditions"]
    for item in evidence.values():
        with gzip.open(tmp_path / item["capture_path"], "rt") as fh:
            assert fh.read()

    quote = Catalog.load(target).price("wellcome", [{"product_id": by_title["Jasmine Rice 8KG"]["id"], "quantity": 1}],
                                       "ctx_wellcome_click_collect")
    assert quote["total_minor"] == 8990


def test_store_without_observed_fee_gets_no_delivery_context(tmp_path):
    # A Wellcome run where no captured page shows the configured fee wording.
    PAGES[f"{ORIGIN}/p/rice8"].pop("extra")
    try:
        run = make_run(tmp_path)
        catalog = run.build([("rice", "pantry")])
    finally:
        PAGES[f"{ORIGIN}/p/rice8"]["extra"] = FEE_TEXT
    assert catalog["delivery_contexts"] == []
    assert any("wording not found" in line for line in run.report)


def test_blocked_jev_run_is_reported_and_nothing_is_written(tmp_path):
    class Blocked(FakeSession):
        status = "blocked"

    run = make_run(tmp_path, factory=Blocked)
    with pytest.raises(CaptureError, match="status 'blocked'"):
        run.build([("rice", "pantry")])
    assert not (tmp_path / "data").exists()
    assert FakeSession.opened == []
