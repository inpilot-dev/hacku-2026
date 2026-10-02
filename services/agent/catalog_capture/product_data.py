"""Store-independent reading of product pages.

Prices come from the page's schema.org Product JSON-LD, which many shops publish
for search engines, and must also appear in the page's visible text. Nothing here
knows about a particular store's layout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Titles that look alcoholic. Used to mark a listing found under a non-alcohol
# search term as "conflicting" (e.g. "rice" also matches "Shaoxing Rice Wine").
ALCOHOL_WORDS = re.compile(
    r"\b(beer|wine|sake|whisk(?:e)?y|vodka|gin|rum|brandy|cider|soju|liqueur|champagne|lager)\b"
    r"|啤酒|葡萄酒|清酒|米酒|紹興酒|白酒",
    re.I,
)


def looks_alcoholic(title: str) -> bool:
    return bool(ALCOHOL_WORDS.search(title))


def to_minor(amount: str | int | float) -> int:
    """HKD amount ("89.90", "1,234", 98, "5.9") to integer cents, without float arithmetic."""
    text = str(amount).replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise ValueError(f"Not a plain HKD amount: {amount!r}")
    whole, _, cents = text.partition(".")
    return int(whole) * 100 + int(cents.ljust(2, "0") or 0)


@dataclass(frozen=True)
class Offer:
    name: str
    price_minor: int
    availability: str | None  # schema.org availability URL/term, None when not published


def _nodes(data) -> list:
    if isinstance(data, list):
        return [n for item in data for n in _nodes(item)]
    if isinstance(data, dict):
        if "@graph" in data:
            return _nodes(data["@graph"])
        return [data]
    return []


def _is_product(node: dict) -> bool:
    kind = node.get("@type")
    return kind == "Product" or (isinstance(kind, list) and "Product" in kind)


def product_offer(ld_blocks: list[str]) -> Offer | None:
    """The single HKD offer of the page's Product, or None.

    Pages with no Product, several different priced Products, an AggregateOffer,
    a price range, or a non-HKD price are rejected rather than guessed.
    """
    found: list[Offer] = []
    for block in ld_blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _nodes(data):
            if not _is_product(node):
                continue
            offers = node.get("offers")
            offers = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
            priced = [o for o in offers if isinstance(o, dict) and o.get("@type", "Offer") == "Offer"
                      and o.get("price") not in (None, "") and o.get("priceCurrency") == "HKD"]
            if len(priced) != 1 or len(priced) != len(offers):
                continue
            try:
                price = to_minor(priced[0]["price"])
            except ValueError:
                continue
            availability = priced[0].get("availability")
            found.append(Offer(str(node.get("name") or "").strip(), price, availability or None))
    distinct = {(o.name, o.price_minor) for o in found}
    return found[0] if len(distinct) == 1 else None


def availability_known(offer: Offer) -> bool | None:
    """True/False from schema.org availability, None when the page did not publish it."""
    if not offer.availability:
        return None
    term = offer.availability.rsplit("/", 1)[-1].lower()
    if term in ("instock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "presale"):
        return True
    if term in ("outofstock", "soldout", "discontinued"):
        return False
    return None


def price_visible(body_text: str, price_minor: int) -> bool:
    """Whether the price appears in the page's visible text, ignoring whitespace.

    Shops often split prices across elements ("$89 .90"), so whitespace is removed
    before matching. Whole-dollar prices may be shown without cents ("$178").
    """
    text = re.sub(r"\s+", "", body_text)
    whole, cents = divmod(price_minor, 100)
    forms = {f"{whole}.{cents:02d}", f"{whole:,}.{cents:02d}"}
    if any(form in text for form in forms):
        return True
    if cents == 0:
        return bool(re.search(rf"\$(?:{whole}|{whole:,})(?![\d.,])", text))
    return False


def matching_links(links: list[dict], term: str, origin: str) -> list[dict]:
    """Same-site links whose visible text contains every word of the term, de-duplicated by URL."""
    words = [w for w in term.lower().split() if w]
    seen: set[str] = set()
    out = []
    for link in links:
        href = (link.get("href") or "").split("#", 1)[0]
        text = (link.get("text") or "").lower()
        if not href.startswith(origin) or href in seen or not words:
            continue
        if all(word in text for word in words):
            seen.add(href)
            out.append({"href": href, "text": link.get("text", "").strip()})
    return out
