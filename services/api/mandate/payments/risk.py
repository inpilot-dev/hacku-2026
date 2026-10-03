"""Risk scoring: purchases that fit every hard rule but look unusual. No I/O.

A points scorecard, the way card issuers score authorizations: each signal
adds points with a plain reason ("This basket is 3.4x the usual ..."), and a
total at or above ``REVIEW_SCORE`` sends the purchase to the owner with every
contributing reason. A single weak signal (a new shop, a new item) never
interrupts on its own; two together, or one strong one (a price jump, a
basket far above usual), do. Scores never refuse: the wallet treats the
reasons as review reasons in the normal expiring approval flow. Each signal
has its own rule id (``<mandate>/v<n>/risk:<check>``), so an approval waives
only the signals the owner saw.

"Usual" is the owner's median paid basket, shrunk toward a prior of
``PRIOR_CAP_SHARE`` of the order cap while history is short, so a brand-new
owner is judged against a sensible default instead of not at all.

The scorecard runs only on mandates with ``policy.risk_review`` on. The
listing check runs on every purchase and always escalates: text in a product
listing that tries to instruct the shopping agent is never normal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from .clock import HKT, parse
from .policy import money, violation

CODE = "RISK_REVIEW_REQUIRED"

REVIEW_SCORE = 50        # total points that send a purchase to the owner
PRIOR_WEIGHT = 3         # the prior counts as this many past purchases
PRIOR_CAP_SHARE = 0.25   # a new owner's expected basket, as a share of the order cap
MIN_HISTORY = 3          # past purchases before habits (new items, odd hours) mean anything
NEAR_CAP_SHARE = 0.9     # basket at least this share of the order cap
PRICE_JUMP_RATIO = 1.25  # unit price at least 25% above the last price paid
SPLIT_SHARE = 0.7        # orders at least this share of the approval threshold, but under it
BURST_COUNT = 3          # purchases on this mandate within BURST_WINDOW before this one
BURST_WINDOW = timedelta(hours=1)
BURN_SHARE = 0.8         # period budget used after this purchase...
BURN_ELAPSED = 0.4       # ...while at most this share of the period has passed
ODD_HOURS = range(0, 6)  # HKT hours an owner rarely shops in

# (ratio of basket to usual, points), strongest first
BASKET_TIERS = [(4, 50), (3, 35), (2, 20), (1.5, 10)]
POINTS = {"near_cap": 20, "new_merchant": 25, "new_items": 15, "price_jump": 50, "split_order": 50,
          "burst": 25, "budget_burn": 25, "odd_hour": 15}

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
    """A paid purchase by the same owner: its shop, total, the unit price paid for each product and when."""
    merchant_id: str
    total_minor: int
    unit_prices: dict[str, int]
    at: datetime | None = None


@dataclass(frozen=True)
class RecentPurchase:
    """A reserved or paid purchase under this mandate: when, and how much."""
    at: datetime
    total_minor: int


@dataclass
class Assessment:
    """The score, its threshold, and the review reasons to show (empty below the threshold)."""
    score: int = 0
    threshold: int = REVIEW_SCORE
    signals: list[dict] = field(default_factory=list)  # every contributing signal with its points
    reasons: list[dict] = field(default_factory=list)  # violations the wallet adds to the decision

    def summary(self) -> dict | None:
        if not self.signals and not self.reasons:
            return None
        return {"score": self.score, "threshold": self.threshold,
                "signals": [{"check": s["check"], "points": s["points"]} for s in self.signals]}


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


def usual_basket(history: list[PastPurchase], cap: int) -> int:
    """The owner's median paid basket, shrunk toward PRIOR_CAP_SHARE of the cap while history is short."""
    prior = PRIOR_CAP_SHARE * cap
    if not history:
        return int(prior)
    n = len(history)
    return int((n * median(p.total_minor for p in history) + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT))


def assess(mandate: dict, quote: dict, *, history: list[PastPurchase], listings: dict[str, str], habits: bool,
           recent: list[RecentPurchase] | None = None, budgets: list[dict] | None = None,
           now: datetime | None = None) -> Assessment:
    """Score ``quote`` under the leaf ``mandate``.

    ``history`` is the owner's paid purchases, oldest first. ``recent`` is this
    mandate's reserved or paid purchases. ``budgets`` are the leaf's current
    budget period rows. ``listings`` maps product id to its listing text.
    """
    out = Assessment()
    for item in quote["items"]:
        snippet = injection_snippet(listings.get(item["product_id"], ""))
        if snippet:
            out.reasons.append(_flag(mandate, f"listing_text:{item['product_id']}",
                                     f"The listing for {item['title']} contains text aimed at the shopping agent "
                                     f"(\"{snippet}\"). Check the item before approving."))
    if not habits:
        return out

    policy, amount, recent = mandate["policy"], quote["total_minor"], recent or []
    cap = policy["per_order_limit_minor"]
    signals: list[tuple[int, dict]] = []

    def add(check: str, points: int, message: str, actual: int | None = None, limit: int | None = None):
        signals.append((points, _flag(mandate, check, message, actual, limit)))
        out.signals.append({"check": check, "points": points})

    usual = usual_basket(history, cap)
    ratio = round(amount / usual, 1) if usual else 0  # score the ratio the owner reads
    points = next((p for r, p in BASKET_TIERS if ratio >= r), 0)
    if points:
        basis = "the usual" if len(history) >= MIN_HISTORY else "the expected"
        add("large_basket", points, f"This basket is {money(amount)}, {ratio:.1f}x {basis} {money(usual)}.",
            amount, usual)

    if amount >= NEAR_CAP_SHARE * cap:
        add("near_cap", POINTS["near_cap"],
            f"It uses {amount / cap:.0%} of the {money(cap)} per-order limit.", amount, cap)

    if history and quote["merchant_id"] not in {p.merchant_id for p in history}:
        add("new_merchant", POINTS["new_merchant"],
            f"First order from {quote['merchant_id']}; earlier orders were all from other shops.")

    if len(history) >= MIN_HISTORY:
        bought = set().union(*(p.unit_prices for p in history))
        new = [item["title"] for item in quote["items"] if item["product_id"] not in bought]
        if new:
            add("new_items", POINTS["new_items"], f"Never bought before: {', '.join(new)}.")

    last_price: dict[str, int] = {}
    for p in history:
        last_price.update(p.unit_prices)
    for item in quote["items"]:
        before, price = last_price.get(item["product_id"]), item["unit_price_minor"]
        if before and price >= PRICE_JUMP_RATIO * before:
            add(f"price_jump:{item['product_id']}", POINTS["price_jump"],
                f"{item['title']} costs {money(price)}, up {round((price / before - 1) * 100)}% "
                f"from {money(before)} last time.", price, before)

    if now is not None:
        threshold = policy.get("approval_above_minor")
        day = [r for r in recent if now - r.at <= timedelta(hours=24)]
        if threshold and SPLIT_SHARE * threshold <= amount <= threshold:
            near = [r for r in day if SPLIT_SHARE * threshold <= r.total_minor <= threshold]
            if near:
                add("split_order", POINTS["split_order"],
                    f"{len(near) + 1} orders just under the {money(threshold)} approval limit in 24 hours "
                    f"({money(sum(r.total_minor for r in near) + amount)} together), which looks like one order "
                    f"split to skip approval.", amount, threshold)

        hour = [r for r in recent if now - r.at <= BURST_WINDOW]
        if len(hour) >= BURST_COUNT:
            add("burst", POINTS["burst"], f"{len(hour) + 1} purchases on this mandate within an hour.")

        for b in budgets or []:
            start, end = parse(b["starts_at"]), parse(b["ends_at"])
            used = b["paid_minor"] + b["reserved_minor"] + amount
            elapsed = (now - start) / (end - start)
            if used >= BURN_SHARE * b["limit_minor"] and elapsed <= BURN_ELAPSED:
                label = "week" if b["period"] == "calendar_week" else "month"
                add(f"budget_burn:{b['period']}", POINTS["budget_burn"],
                    f"This would use {used / b['limit_minor']:.0%} of the {label}'s {money(b['limit_minor'])} "
                    f"budget with {1 - elapsed:.0%} of the {label} still to go.", used, b["limit_minor"])
                break

        timed = [p.at for p in history if p.at is not None]
        local = now.astimezone(HKT).hour
        if (local in ODD_HOURS and len(timed) >= MIN_HISTORY
                and not any(t.astimezone(HKT).hour in ODD_HOURS for t in timed)):
            add("odd_hour", POINTS["odd_hour"],
                f"Ordered at {now.astimezone(HKT):%H:%M}; earlier orders were all placed during the day.")

    out.score = sum(points for points, _ in signals)
    if out.score >= REVIEW_SCORE:
        out.reasons += [v for _, v in sorted(signals, key=lambda s: -s[0])]
    return out
