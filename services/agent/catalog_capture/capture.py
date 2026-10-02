"""Capture an observed Wellcome catalog in the wallet's catalog file shape.

    python -m catalog_capture --out ../../data/catalog

Writes ``wellcome.json`` (see services/api/mandate/payments/catalog.py for the
shape) and gzipped copies of every fetched page under
``evidence/wellcome-<stamp>/``. Each evidence record's ``capture_path`` points at
the raw page its value was read from. Nothing is written if a cross-check fails.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import wellcome

HKT = timezone(timedelta(hours=8))
USER_AGENT = "MandateCatalogCapture/0.1 (HacKU 2026 student project; low-volume price observation)"
MERCHANT_ID = "wellcome"
PICKUP_CONTEXT_ID = "ctx_wellcome_click_collect"
REQUEST_DELAY_S = 2.0


class CaptureError(Exception):
    pass


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise CaptureError(f"{url} returned HTTP {response.status}")
        return response.read().decode("utf-8")


class Run:
    """One capture run: fetched pages are kept in memory until every check passes."""

    def __init__(self, repo_root: Path, out_dir: Path, fetcher=fetch, now=None, delay_s: float = REQUEST_DELAY_S):
        self.now = now or datetime.now(HKT).replace(microsecond=0)
        self.stamp = self.now.strftime("%Y%m%dT%H%M%S")
        self.repo_root = repo_root.resolve()
        self.out_dir = out_dir.resolve()
        self.evidence_dir = self.out_dir / "evidence" / f"wellcome-{self.stamp}"
        self.fetcher = fetcher
        self.delay_s = delay_s
        self.pages: dict[str, tuple[str, str]] = {}  # name -> (url, html)
        self.observed: dict[str, str] = {}  # name -> observed_at

    def get(self, name: str, url: str) -> str:
        if self.pages:
            time.sleep(self.delay_s)
        page = self.fetcher(url)
        self.pages[name] = (url, page)
        self.observed[name] = datetime.now(HKT).replace(microsecond=0).isoformat()
        return page

    def capture_path(self, name: str) -> str:
        return (self.evidence_dir / f"{name}.html.gz").relative_to(self.repo_root).as_posix()

    def evidence(self, ev_id: str, name: str, kind: str, conditions: str, source_url: str | None = None) -> dict:
        return {
            "id": ev_id,
            "source_url": source_url or self.pages[name][0],
            "observed_at": self.observed[name],
            "kind": kind,
            "capture_path": self.capture_path(name),
            "conditions": conditions,
        }

    def build(self, categories: dict[str, str] | None = None) -> dict:
        categories = categories or wellcome.CATEGORIES
        evidence: list[dict] = []
        products: list[dict] = []
        checks: list[tuple[dict, str]] = []

        self.get("home", wellcome.BASE_URL + "/en")
        merchant_ev = f"ev_wellcome_identity_{self.stamp}"
        evidence.append(self.evidence(
            merchant_ev, "home", "merchant_identity",
            "Wellcome online shop home page (www.wellcome.com.hk), English, guest session.",
        ))

        for category_id, category in categories.items():
            name = f"category-{category_id}"
            page = self.get(name, wellcome.category_url(category_id))
            title = wellcome.category_title(page)
            listings = wellcome.parse_category(page)
            if not listings:
                raise CaptureError(f"No priced listings found in category {category_id}; layout may have changed.")
            category_ev = f"ev_wellcome_cat_{category_id}_{self.stamp}"
            evidence.append(self.evidence(
                category_ev, name, "category",
                f"Curated mapping: Wellcome category '{title}' ({category_id}) -> {category}. "
                "Applies to listings shown on page 1 of this category.",
            ))
            for item in listings:
                earlier = next((p for p in products if p["id"] == f"wellcome_{item.sku}"), None)
                if earlier is not None:
                    # Listed under two Wellcome categories. If they map differently the
                    # category is not trustworthy; "conflicting" sends it to human review.
                    if earlier["category"] != category:
                        earlier["category_status"] = "conflicting"
                        earlier["evidence_ids"].append(category_ev)
                    continue
                price_ev = f"ev_wellcome_{item.sku}_{self.stamp}"
                conditions = [
                    f"Listed price on page 1 of Wellcome category '{title}', guest session (not logged in), "
                    "no delivery address or membership selected.",
                    f"Product page: {item.product_url}.",
                ]
                if item.was_price_minor is not None:
                    conditions.append(f"Struck-through previous price shown: HK${item.was_price_minor / 100:.2f}.")
                conditions.append("Multi-buy promotions were not captured; the single-unit price is used.")
                evidence.append(self.evidence(price_ev, name, "product_price", " ".join(conditions)))
                status = "curated"
                if category != "alcohol" and wellcome.looks_alcoholic(item.title):
                    status = "conflicting"
                product = {
                    "id": f"wellcome_{item.sku}",
                    "merchant_id": MERCHANT_ID,
                    "title": item.title,
                    "description": item.title,
                    "category": category,
                    "category_status": status,
                    "unit_label": "pack",
                    "unit_price_minor": item.price_minor,
                    "currency": "HKD",
                    "available": item.available,
                    "evidence_ids": [price_ev, category_ev],
                }
                products.append(product)
                if item.available and not any(c[0]["category"] == category for c in checks):
                    checks.append((product, item.product_url))  # first available listing per category

        fee_name = self._cross_check(checks)
        fee_ev = f"ev_wellcome_pickup_fee_{self.stamp}"
        evidence.append(self.evidence(
            fee_ev, fee_name, "delivery_fee",
            f"Shown on the Wellcome product page as: '{wellcome.FREE_PICKUP_TEXT}' "
            "Free Click & Collect store pickup applies to orders over HK$50. "
            "The charge for pickup orders of HK$50 or less was not observed.",
        ))
        return {
            "_note": f"Observed Wellcome.com.hk snapshot captured {self.now.isoformat()} by services/agent/catalog_capture.",
            "merchants": {MERCHANT_ID: {"revision": f"wellcome-{self.stamp}", "data_mode": "observed_snapshot",
                                        "evidence_ids": [merchant_ev]}},
            "products": products,
            "delivery_contexts": [{
                "id": PICKUP_CONTEXT_ID,
                "merchant_id": MERCHANT_ID,
                "fee_rules": [{
                    "label": "Click & Collect pickup (free on orders over HK$50)",
                    "min_subtotal_minor": 5001,
                    "max_subtotal_minor": None,
                    "amount_minor": 0,
                    "evidence_ids": [fee_ev],
                }],
            }],
            "evidence": evidence,
        }

    def _cross_check(self, checks: list[tuple[dict, str]]) -> str:
        """Compare category-page prices with product-page JSON-LD; return a product page name for fee evidence.

        Fetches one product page per captured category. Any mismatch aborts the run,
        because it means the category parser read the wrong element.
        """
        fee_name = None
        for product, url in checks:
            name = f"product-{product['id'].removeprefix('wellcome_')}"
            page = self.get(name, url)
            listed = wellcome.json_ld_price_minor(page)
            if listed != product["unit_price_minor"]:
                raise CaptureError(
                    f"{product['id']}: category page price {product['unit_price_minor']} != product page JSON-LD {listed}."
                )
            if fee_name is None and wellcome.FREE_PICKUP_TEXT in page:
                fee_name = name
        if fee_name is None:
            raise CaptureError("Click & Collect fee text not found on any checked product page.")
        return fee_name

    def write(self, catalog: dict) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=False)
        for name, (_url, page) in self.pages.items():
            with gzip.open(self.evidence_dir / f"{name}.html.gz", "wt", encoding="utf-8") as fh:
                fh.write(page)
        target = self.out_dir / "wellcome.json"
        target.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target
