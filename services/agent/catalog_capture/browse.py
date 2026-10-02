"""Store-independent catalog capture: Jev navigates, product pages supply the prices.

    python -m catalog_capture.browse --store wellcome --item rice=pantry --item broccoli=produce

For each shopping term, Jev starts on the store's home page and navigates to a
listing for that term. Same-site links whose text matches the term are opened
in the same tab. A product is kept only if its page publishes one HKD price in
schema.org Product data and that price also appears in the page's visible text.

A store is configuration only: a start URL and the exact wording of any
delivery or pickup fee it publishes. A fee is captured only when that wording
is found on a captured page; otherwise the store gets no delivery context, so
its products can be listed but not quoted.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .capture import CaptureError, Run
from .product_data import availability_known, looks_alcoholic, matching_links, price_visible, product_offer

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORIES = {"produce", "dairy", "eggs", "meat", "seafood", "bakery", "pantry",
              "beverage_non_alcoholic", "alcohol", "household", "unknown"}  # contract Category enum

GOAL = ("Find products matching '{term}' in this online shop. Open the most relevant category or product "
        "listing, and stop when products matching '{term}' with prices are visible. Do not add anything "
        "to a cart, sign in, or start checkout.")


@dataclass(frozen=True)
class FeeRule:
    context_id: str
    label: str
    page_text: str  # exact wording that must appear on a captured page
    min_subtotal_minor: int
    max_subtotal_minor: int | None
    amount_minor: int
    unobserved: str  # what this rule does not cover, recorded in the evidence


@dataclass(frozen=True)
class Store:
    name: str
    home: str
    origin: str
    fees: tuple[FeeRule, ...] = field(default_factory=tuple)


STORES = {
    "wellcome": Store(
        "Wellcome", "https://www.wellcome.com.hk/en", "https://www.wellcome.com.hk",
        (FeeRule("ctx_wellcome_click_collect", "Click & Collect pickup (free on orders over HK$50)",
                 "Enjoy our free Click & collect service on orders over HK$50.", 5001, None, 0,
                 "The charge for pickup orders of HK$50 or less was not observed."),),
    ),
    "hktvmall": Store("HKTVmall", "https://www.hktvmall.com/hktv/en/", "https://www.hktvmall.com"),
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class BrowseRun(Run):
    def __init__(self, repo_root: Path, out_dir: Path, store_id: str, session_factory=None, now=None,
                 max_per_term: int = 3):
        super().__init__(repo_root, out_dir, fetcher=None, now=now, delay_s=0, prefix=f"browse-{store_id}")
        if session_factory is None:
            from .jev_adapter import JevSession
            session_factory = JevSession
        self.store_id = store_id
        self.store = STORES[store_id]
        self.session_factory = session_factory
        self.max_per_term = max_per_term
        self.report: list[str] = []

    def build(self, items: list[tuple[str, str]]) -> dict:
        products: list[dict] = []
        evidence: list[dict] = []
        merchant_ev = f"ev_{self.store_id}_identity_{self.stamp}"

        for term, category in items:
            with self.session_factory(self.store.home, GOAL.format(term=term)) as session:
                if "home" not in self.pages:
                    home = session.snapshot()
                    self.keep("home", home["url"], home["html"])
                    evidence.append(self.evidence(
                        merchant_ev, "home", "merchant_identity",
                        f"{self.store.name} online shop home page as rendered in a Steel browser session, guest "
                        "(not logged in).",
                    ))
                status = session.run()
                listing = session.snapshot()
                if status != "done":
                    self.report.append(f"{term}: Jev stopped with status '{status}' at {listing['url']}")
                    continue
                listing_name = f"listing-{_slug(term)}"
                self.keep(listing_name, listing["url"], listing["html"])
                category_ev = f"ev_{self.store_id}_term_{_slug(term)}_{self.stamp}"
                evidence.append(self.evidence(
                    category_ev, listing_name, "category",
                    f"Operator mapping for search term '{term}' -> {category}; not stated by the store. "
                    f"Jev reached this listing for the term.",
                ))
                kept, skipped = self._products(session, term, category, listing["url"], category_ev,
                                               products, evidence)
                self.report.append(f"{term}: {kept} product(s) from {listing['url']}"
                                   + (f"; skipped {'; '.join(skipped)}" if skipped else ""))

        if not products:
            raise CaptureError("No products captured. " + " | ".join(self.report))
        contexts = self._fees(evidence)
        return {
            "_note": f"Browser-observed {self.store.name} snapshot captured {self.now.isoformat()} by "
                     "services/agent/catalog_capture.browse (Jev navigation, schema.org product data).",
            "_report": self.report,
            "merchants": {self.store_id: {"revision": f"browse-{self.store_id}-{self.stamp}",
                                          "data_mode": "observed_snapshot", "evidence_ids": [merchant_ev]}},
            "products": products,
            "delivery_contexts": contexts,
            "evidence": evidence,
        }

    def _products(self, session, term, category, listing_url, category_ev, products, evidence):
        kept, skipped, visits = 0, [], 0
        for link in matching_links(session.links(), term, self.store.origin):
            if kept >= self.max_per_term or visits >= self.max_per_term * 3:
                break
            visits += 1
            page = session.open(link["href"])
            offer = product_offer(page["ld"])
            if offer is None:
                skipped.append(f"{link['text'][:40]!r}: no single HKD product offer")
                continue
            if not price_visible(page["text"], offer.price_minor):
                skipped.append(f"{link['text'][:40]!r}: price not in visible text")
                continue
            product_id = f"{self.store_id}_{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}"
            if any(p["id"] == product_id for p in products):
                continue
            name = f"product-{product_id.removeprefix(self.store_id + '_')}"
            self.keep(name, page["url"], page["html"])
            title = offer.name or link["text"]
            available = availability_known(offer)
            availability_note = ("Availability is not published in the product data; the product page showed a price."
                                 if available is None else f"schema.org availability: {offer.availability}.")
            price_ev = f"ev_{product_id}_{self.stamp}"
            evidence.append(self.evidence(
                price_ev, name, "product_price",
                "Price from the product page's schema.org Product data, also shown in its visible text. "
                "Steel browser session, guest (not logged in), no delivery address selected. "
                f"Reached by Jev from {listing_url} for search term '{term}'. {availability_note}",
            ))
            conflicting = category != "alcohol" and looks_alcoholic(title)
            products.append({
                "id": product_id, "merchant_id": self.store_id, "title": title, "description": title,
                "category": category, "category_status": "conflicting" if conflicting else "curated",
                "unit_label": "pack", "unit_price_minor": offer.price_minor, "currency": "HKD",
                "available": available is not False, "evidence_ids": [price_ev, category_ev],
            })
            kept += 1
        return kept, skipped

    def _fees(self, evidence: list[dict]) -> list[dict]:
        contexts = []
        for rule in self.store.fees:
            name = next((n for n, (_url, page) in self.pages.items() if rule.page_text in page), None)
            if name is None:
                self.report.append(f"fee '{rule.label}': wording not found on captured pages; no delivery context")
                continue
            fee_ev = f"ev_{rule.context_id}_{self.stamp}"
            evidence.append(self.evidence(
                fee_ev, name, "delivery_fee", f"Shown on the page as: '{rule.page_text}' {rule.unobserved}"))
            contexts.append({"id": rule.context_id, "merchant_id": self.store_id, "fee_rules": [{
                "label": rule.label, "min_subtotal_minor": rule.min_subtotal_minor,
                "max_subtotal_minor": rule.max_subtotal_minor, "amount_minor": rule.amount_minor,
                "evidence_ids": [fee_ev]}]})
        if not self.store.fees:
            self.report.append("no published fee wording configured; products can be listed but not quoted")
        return contexts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser-observed catalog capture (Jev + Steel).")
    parser.add_argument("--store", choices=sorted(STORES), required=True)
    parser.add_argument("--item", action="append", required=True, metavar="TERM=CATEGORY",
                        help="Shopping term and its contract category, e.g. rice=pantry. Repeatable.")
    parser.add_argument("--max", type=int, default=3, help="Products kept per term (default 3).")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "catalog")
    args = parser.parse_args(argv)
    items = []
    for item in args.item:
        term, sep, category = item.partition("=")
        if not sep or not term.strip() or category.strip() not in CATEGORIES:
            parser.error(f"--item must look like TERM=CATEGORY with a contract category, got {item!r}")
        items.append((term.strip(), category.strip()))

    run = BrowseRun(REPO_ROOT, args.out, args.store, max_per_term=args.max)
    try:
        catalog = run.build(items)
    except CaptureError as exc:
        print(f"Capture failed, nothing written: {exc}", file=sys.stderr)
        return 1
    target = run.write(catalog, f"browse-{args.store}.json")
    for line in run.report:
        print(" -", line)
    print(f"Wrote {len(catalog['products'])} products and {len(run.pages)} evidence pages to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
