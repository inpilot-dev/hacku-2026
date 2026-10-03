"""Parse Wellcome.com.hk (and Market Place) server-rendered pages into observed listings.

Wellcome renders category pages on the server (Nuxt SSR), so a plain HTTP GET
returns the product cards with their prices. Market Place (marketplacehk.com) is
the same DFI "superweb" build: identical category IDs, card markup, product
JSON-LD and fee wording (checked 2026-10-03), so a `Shop` selects the site. Nothing here runs a browser or a
model: prices are read from fixed page elements and cross-checked against the
product page's schema.org JSON-LD before a catalog is written.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from .product_data import looks_alcoholic  # noqa: F401  (re-exported for the static capture)


@dataclass(frozen=True)
class Shop:
    merchant_id: str
    name: str
    base_url: str
    pickup_context_id: str


WELLCOME = Shop("wellcome", "Wellcome", "https://www.wellcome.com.hk", "ctx_wellcome_click_collect")
MARKETPLACE = Shop("marketplace", "Market Place", "https://www.marketplacehk.com", "ctx_marketplace_click_collect")
SHOPS = {shop.merchant_id: shop for shop in (WELLCOME, MARKETPLACE)}

BASE_URL = WELLCOME.base_url

# Curated mapping of Wellcome top-level categories to contract categories.
# Only categories whose contents map to one contract category are listed.
# Alcohol is captured so a blocked-category refusal can be demonstrated.
CATEGORIES = {
    "100020": "pantry",                  # Rice, Oil & Noodles
    "100011": "produce",                 # Fruits & Vegetables
    "100002": "beverage_non_alcoholic",  # Beverages (alcohol is its own category, 100001)
    "100001": "alcohol",                 # Alcohol
}

SOLD_OUT = re.compile(r"sold.?out|out.?of.?stock|售罄|缺貨", re.I)

FREE_PICKUP_TEXT = "Enjoy our free Click & collect service on orders over HK$50."
FREE_DELIVERY_TEXT = "Enjoy free delivery to your door on orders over HK$500."


def category_url(category_id: str, base_url: str = BASE_URL) -> str:
    return f"{base_url}/en/category/{category_id}/1.html"


@dataclass(frozen=True)
class Listing:
    sku: str
    title: str
    product_url: str
    price_minor: int
    was_price_minor: int | None
    available: bool


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _minor(dollars: str, cents: str | None = None) -> int:
    whole = dollars.replace(",", "")
    if "." in whole:
        whole, cents = whole.split(".", 1)
    cents = (cents or "0").ljust(2, "0")[:2]
    return int(whole) * 100 + int(cents)


def category_title(page: str) -> str:
    match = re.search(r'class="category-big-title"[^>]*>(.*?)</div>', page, re.S)
    if not match:
        raise ValueError("Category title not found; the page layout may have changed.")
    return _text(match.group(1))


def parse_category(page: str, base_url: str = BASE_URL) -> list[Listing]:
    """Return one Listing per product card. Cards without a readable price are skipped."""
    starts = [m.start() for m in re.finditer(r'<div class="ware-wrapper"', page)]
    listings: dict[str, Listing] = {}
    for index, start in enumerate(starts):
        card = page[start:starts[index + 1] if index + 1 < len(starts) else len(page)]
        link = re.search(r'<a href="(/en/wellcome/p/[^"]+/i/(\d+)\.html)"', card)
        title = re.search(r'class="promo"[^>]*>(.*?)</div>', card, re.S)
        # The struck-through old price comes first in the card, so the current
        # price is read from its own element rather than the first "$" amount.
        current = re.search(
            r'class="current-price"[^>]*>\s*<div class="price"[^>]*>\$([\d,]+(?:\.\d{1,2})?)</div>'
            r'(?:\s*<div class="small-price"[^>]*>\.(\d{1,2})</div>)?',
            card,
        )
        if not (link and title and current):
            continue
        was = re.search(r'class="line-price"[^>]*>\s*\$([\d,]+(?:\.\d{1,2})?)\s*<', card)
        sku = link.group(2)
        listings.setdefault(sku, Listing(
            sku=sku,
            title=_text(title.group(1)),
            product_url=base_url + link.group(1),
            price_minor=_minor(current.group(1), current.group(2)),
            was_price_minor=_minor(was.group(1)) if was else None,
            available="addCart" in card and not SOLD_OUT.search(_text(card)),
        ))
    return list(listings.values())


def json_ld_price_minor(page: str) -> int | None:
    """The Offer price from the product page's schema.org Product JSON-LD."""
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            offer = data.get("offers") or {}
            if offer.get("priceCurrency") == "HKD" and offer.get("price") is not None:
                return _minor(str(offer["price"]))
    return None


