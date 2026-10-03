"""Risk checks: purchases that fit every hard rule but look unusual. No I/O.

Each check returns a ``RISK_REVIEW_REQUIRED`` violation with a plain reason
("This basket is 3.4x the usual ..."). They never refuse: the wallet treats
them as review reasons, so the purchase waits in the normal expiring approval
flow and the owner reads the reasons before deciding. Each check has its own
rule id (``<mandate>/v<n>/risk:<check>``), so an approval waives only the
checks the owner saw.

The habit checks (first purchase, new shop, large basket, new items, price jump) run
only on mandates with ``policy.risk_review`` on. The listing check runs on
every purchase: text in a product listing that tries to instruct the shopping
agent is never normal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from .policy import money, violation

CODE = "RISK_REVIEW_REQUIRED"

MIN_HISTORY = 3        # past purchases before "usual" means anything
LARGE_BASKET_RATIO = 3  # total at least this many times the median past total
PRICE_JUMP_RATIO = 1.25  # unit price at least 25% above the last price paid

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|all|your|other)\b.{0,20}"
    r"\b(instructions?|rules?|prompts?|limits?|guidelines?)",
    r"\bsystem\s*prompt\b",
    r"(^|\n|\s)(system|assistant)\s*:",
    r"<\|?\s*(im_start|im_end|system)\s*\|?>|\[/?INST\]",
    r"\b(you are|act as|as) (an? )?(ai|assistant|agent|language model|llm|shopping agent)\b",
    r"\b(override|bypass|skip|disable)\b.{0,20}\b(limits?|polic(y|ies)|mandate|budget|approvals?|checks?)\b",
    r"\b(without|no need for)\b.{0,20}\b(approval|asking|confirmation|permission)\b",
    r"\b(do not|don't|never)\b.{0,10}\b(tell|inform|ask|notify)\b.{0,20}\b(user|owner|caregiver|family|parent)\b",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class PastPurchase:
    """A paid purchase by the same owner: its shop, total and the unit price paid for each product."""
    merchant_id: str
    total_minor: int
    unit_prices: dict[str, int]


def injection_snippet(text: str) -> str | None:
    """The suspicious part of listing text, or None if it reads like a normal listing."""
    match = _INJECTION.search(text or "")
    if match is None:
        return None
    start = max(0, match.start() - 10)
    snippet = " ".join(text[start:match.end() + 30].split())
    return snippet if len(snippet) <= 80 else snippet[:77] + "..."


def _flag(mandate: dict, check: str, message: str, actual: int | None = None, limit: int | None = None) -> dict:
    return violation(CODE, mandate, f"risk:{check}", message, actual, limit)


def assess(mandate: dict, quote: dict, *, history: list[PastPurchase], mandate_purchases: int,
           listings: dict[str, str], habits: bool) -> list[dict]:
    """Review reasons for ``quote`` under the leaf ``mandate``.

    ``history`` is the owner's paid purchases, oldest first. ``mandate_purchases``
    counts reserved or paid purchases already made under this mandate.
    ``listings`` maps product id to its listing text (title and description).
    """
    reasons = []
    for item in quote["items"]:
        snippet = injection_snippet(listings.get(item["product_id"], ""))
        if snippet:
            reasons.append(_flag(mandate, f"listing_text:{item['product_id']}",
                                 f"The listing for {item['title']} contains text aimed at the shopping agent "
                                 f"(\"{snippet}\"). Check the item before approving."))
    if not habits:
        return reasons

    amount = quote["total_minor"]
    if mandate_purchases == 0:
        reasons.append(_flag(mandate, "first_purchase",
                             f"This is the first purchase under this mandate ({money(amount)})."))

    if history and quote["merchant_id"] not in {p.merchant_id for p in history}:
        reasons.append(_flag(mandate, "new_merchant", f"First order from {quote['merchant_id']}; earlier orders "
                                                      f"were all from other shops."))

    if len(history) >= MIN_HISTORY:
        usual = int(median(p.total_minor for p in history))
        if usual > 0 and amount >= LARGE_BASKET_RATIO * usual:
            reasons.append(_flag(mandate, "large_basket",
                                 f"This basket is {money(amount)}, {amount / usual:.1f}x the usual "
                                 f"{money(usual)}.", amount, usual))

        bought = set().union(*(p.unit_prices for p in history))
        new = [item["title"] for item in quote["items"] if item["product_id"] not in bought]
        if new:
            reasons.append(_flag(mandate, "new_items", f"Never bought before: {', '.join(new)}."))

    last_price: dict[str, int] = {}
    for p in history:
        last_price.update(p.unit_prices)
    for item in quote["items"]:
        before = last_price.get(item["product_id"])
        now = item["unit_price_minor"]
        if before and now >= PRICE_JUMP_RATIO * before:
            reasons.append(_flag(mandate, f"price_jump:{item['product_id']}",
                                 f"{item['title']} costs {money(now)}, up {round((now / before - 1) * 100)}% "
                                 f"from {money(before)} last time.", now, before))
    return reasons
