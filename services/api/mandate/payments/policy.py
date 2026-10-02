"""Pure policy checks. No I/O: the ledger loads state and calls these inside its transaction.

Every rule applies across the whole delegation chain (leaf first, then each
ancestor): allowed merchants intersect, blocked categories accumulate, caps
and expiries tighten, and every period limit stays in force.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .clock import parse

REVIEW_CODES = {"CATEGORY_REVIEW_REQUIRED", "APPROVAL_REQUIRED"}


def rule_id(mandate: dict, rule: str) -> str:
    return f"{mandate['id']}/v{mandate['version']}/{rule}"


def violation(code: str, mandate: dict, rule: str, message: str, actual: int | None = None, limit: int | None = None) -> dict:
    return {
        "code": code,
        "rule_id": rule_id(mandate, rule),
        "mandate_id": mandate["id"],
        "message": message,
        "actual_minor": actual,
        "limit_minor": limit,
    }


def money(minor: int) -> str:
    return f"HK${minor // 100:,}.{minor % 100:02d}"


# --- confirmation-time validation ------------------------------------------

def validate_policy(policy: dict, now: datetime) -> list[str]:
    problems = []
    try:
        expires = parse(policy["expires_at"])
        if expires <= now:
            problems.append("expires_at must be in the future.")
    except ValueError:
        problems.append("expires_at must be RFC 3339 with an explicit offset.")
    periods = [p["period"] for p in policy["period_limits"]]
    if len(periods) != len(set(periods)):
        problems.append("Each period may be defined only once.")
    if len(policy["allowed_merchant_ids"]) != len(set(policy["allowed_merchant_ids"])):
        problems.append("allowed_merchant_ids must be unique.")
    if len(policy["blocked_categories"]) != len(set(policy["blocked_categories"])):
        problems.append("blocked_categories must be unique.")
    return problems


def narrowing_problems(child: dict, parent: dict) -> list[str]:
    """A child mandate may only narrow its parent's authority."""
    problems = []
    if not set(child["allowed_merchant_ids"]) <= set(parent["allowed_merchant_ids"]):
        problems.append("Child allows merchants the parent does not.")
    if not set(parent["blocked_categories"]) <= set(child["blocked_categories"]):
        problems.append("Child must keep every category the parent blocks.")
    if child["per_order_limit_minor"] > parent["per_order_limit_minor"]:
        problems.append("Child per-order limit exceeds the parent's.")
    if parse(child["expires_at"]) > parse(parent["expires_at"]):
        problems.append("Child expires after the parent.")
    p_threshold, c_threshold = parent["approval_above_minor"], child["approval_above_minor"]
    if p_threshold is not None and (c_threshold is None or c_threshold > p_threshold):
        problems.append("Child approval threshold must not exceed the parent's.")
    return problems


# --- authorization-time evaluation -----------------------------------------

@dataclass
class Evaluation:
    hard: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.hard:
            return "refused"
        if self.review:
            return "requires_review"
        return "approved"

    def add(self, v: dict) -> None:
        (self.review if v["code"] in REVIEW_CODES else self.hard).append(v)


def mandate_state_violation(mandate: dict, now: datetime) -> dict | None:
    if mandate["status"] == "revoked":
        return violation("MANDATE_REVOKED", mandate, "status", f"Mandate {mandate['id']} was revoked.")
    if mandate["status"] != "active":
        return violation("MANDATE_NOT_ACTIVE", mandate, "status", f"Mandate {mandate['id']} is not active.")
    if parse(mandate["expires_at"]) <= now:
        return violation("MANDATE_EXPIRED", mandate, "expires_at", f"Mandate {mandate['id']} expired at {mandate['expires_at']}.")
    return None


def evaluate(chain: list[dict], quote: dict, budgets: list[dict], now: datetime) -> Evaluation:
    """Check a quote against every mandate in the chain and every applicable budget period.

    ``chain`` rows carry a parsed ``policy`` dict. ``budgets`` rows carry
    ``mandate_id, period, limit_minor, paid_minor, reserved_minor``.
    """
    ev = Evaluation()
    amount = quote["total_minor"]
    # Name the fees in the total so a refusal shows when delivery tipped it over.
    fees = [f"{money(c['amount_minor'])} {c['kind']}" for c in quote["charges"] if c["amount_minor"]]
    total_text = money(amount) + (f" (includes {' and '.join(fees)})" if fees else "")

    if parse(quote["expires_at"]) <= now:
        ev.add(violation("QUOTE_EXPIRED", chain[0], "quote", f"Quote {quote['id']} expired at {quote['expires_at']}."))
    if quote["currency"] != "HKD":
        ev.add(violation("QUOTE_CHANGED", chain[0], "currency", "Only HKD quotes are accepted."))

    for m in chain:
        p = m["policy"]
        state = mandate_state_violation(m, now)
        ev.rule_ids.append(rule_id(m, "status"))
        if state:
            ev.add(state)

        ev.rule_ids.append(rule_id(m, "allowed_merchants"))
        if quote["merchant_id"] not in p["allowed_merchant_ids"]:
            ev.add(violation("MERCHANT_NOT_ALLOWED", m, "allowed_merchants",
                             f"{quote['merchant_id']} is not an approved shop."))

        blocked = set(p["blocked_categories"])
        if blocked:
            ev.rule_ids.append(rule_id(m, "blocked_categories"))
        for item in quote["items"]:
            if item["category"] in blocked:
                ev.add(violation("CATEGORY_BLOCKED", m, "blocked_categories",
                                 f"{item['title']} is {item['category']}, which this mandate blocks."))
            elif blocked and (item["category"] == "unknown" or item["category_status"] in ("unknown", "conflicting")):
                ev.add(violation("CATEGORY_REVIEW_REQUIRED", m, "blocked_categories",
                                 f"{item['title']} has an {item['category_status']} category; a person must check it."))

        ev.rule_ids.append(rule_id(m, "per_order_limit"))
        if amount > p["per_order_limit_minor"]:
            ev.add(violation("ORDER_CAP_EXCEEDED", m, "per_order_limit",
                             f"Order total {total_text} is over the {money(p['per_order_limit_minor'])} per-order limit.",
                             amount, p["per_order_limit_minor"]))

        threshold = p["approval_above_minor"]
        if threshold is not None:
            ev.rule_ids.append(rule_id(m, "approval_above"))
            if amount > threshold:
                ev.add(violation("APPROVAL_REQUIRED", m, "approval_above",
                                 f"Orders over {money(threshold)} need the user's approval.", amount, threshold))

    by_id = {m["id"]: m for m in chain}
    for b in budgets:
        m = by_id[b["mandate_id"]]
        rule = f"period:{b['period']}"
        ev.rule_ids.append(rule_id(m, rule))
        available = b["limit_minor"] - b["paid_minor"] - b["reserved_minor"]
        if amount > available:
            ev.add(violation("PERIOD_BUDGET_EXCEEDED", m, rule,
                             f"Order total {total_text} is over the {money(available)} left this "
                             f"{'week' if b['period'] == 'calendar_week' else 'month'}.",
                             amount, available))
    return ev
