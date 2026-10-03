"""POST /mandates/draft: turn a written request into an inactive draft.

Interpretation uses fixed rules, not a model, so the same sentence always gives
the same proposal. Anything the rules cannot read (a store, a limit, an expiry)
becomes an ambiguity question instead of a guess, and the proposal is withheld
until every required field is stated. A draft is never spending authority: the
user confirms an explicit policy through POST /mandates/confirm.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta

from mandate.payments.auth import Actor
from mandate.payments.clock import HKT, iso
from mandate.payments.drafts import InMemoryDrafts
from mandate.payments.errors import conflict
from mandate.payments.service import Wallet

DRAFT_TTL = timedelta(minutes=30)
CATEGORIES = ("produce", "dairy", "eggs", "meat", "seafood", "bakery", "household", "alcohol")
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

_AMOUNT = r"(?:HK)?\$\s?([\d,]+(?:\.\d{1,2})?)"
_ORDER = re.compile(_AMOUNT + r"\s*(?:per|an?|each|for each|for any)\s+(?:single\s+)?(?:order|purchase|basket)", re.I)
_WEEK = re.compile(_AMOUNT + r"\s*(?:per|a|each|every)\s+(?:calendar\s+)?week|" + _AMOUNT + r"\s+weekly", re.I)
_MONTH = re.compile(_AMOUNT + r"\s*(?:per|a|each|every)\s+(?:calendar\s+)?month|" + _AMOUNT + r"\s+monthly", re.I)
_APPROVAL = re.compile(r"(?:ask|check with|approval)[^.]*?(?:over|above|more than)\s+" + _AMOUNT, re.I)
_EXPIRY_DATE = re.compile(r"(?:until|expires?|expiring|through|till|ends?|by)\s+(?:on\s+)?(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:,?\s+(\d{4}))?", re.I)
_EXPIRY_END = re.compile(r"(?:until|expires?|expiring|through|till|by)\s+(?:the\s+)?end\s+of\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?", re.I)


def _minor(amount: str) -> int:
    whole, _, cents = amount.replace(",", "").partition(".")
    return int(whole) * 100 + int(cents.ljust(2, "0") or 0)


def _expiry(text: str, now: datetime) -> tuple[str | None, str | None]:
    """(expires_at, problem). Dates end at 23:59:59 Hong Kong time."""
    day = month = year = None
    if match := _EXPIRY_DATE.search(text):
        day, month, year = int(match.group(1)), MONTHS.get(match.group(2).lower()), match.group(3)
    elif match := _EXPIRY_END.search(text):
        month, year = MONTHS.get(match.group(1).lower()), match.group(2)
    if month is None:
        return None, "When should this permission expire? Give a date such as 31 October 2026."
    year = int(year) if year else now.year
    if day is None:
        day = calendar.monthrange(year, month)[1]
    try:
        expires = datetime(year, month, day, 23, 59, 59, tzinfo=HKT)
    except ValueError:
        return None, "The expiry date is not a real calendar date."
    if expires <= now:
        return None, "The expiry date has already passed; choose a future date."
    return iso(expires), None


def interpret(text: str, merchant_ids: list[str], now: datetime) -> tuple[dict | None, list[dict], str]:
    """(proposed policy or None, ambiguities, summary)."""
    ambiguities: list[dict] = []
    lowered = text.lower()

    order = _ORDER.search(text)
    if not order:
        ambiguities.append({"field": "per_order_limit_minor",
                            "question": "What is the most one order may cost, e.g. HK$300 per order?"})
    periods = []
    if week := _WEEK.search(text):
        periods.append({"period": "calendar_week", "limit_minor": _minor(week.group(1) or week.group(2)),
                        "timezone": "Asia/Hong_Kong"})
    if month := _MONTH.search(text):
        periods.append({"period": "calendar_month", "limit_minor": _minor(month.group(1) or month.group(2)),
                        "timezone": "Asia/Hong_Kong"})
    if not periods:
        ambiguities.append({"field": "period_limits",
                            "question": "What is the weekly or monthly budget, e.g. HK$800 per week?"})

    merchants = [m for m in merchant_ids if re.search(rf"\b{re.escape(m.replace('_', ' '))}\b|\b{re.escape(m)}\b", lowered)]
    if not merchants:
        ambiguities.append({"field": "allowed_merchant_ids",
                            "question": "Which store may the agent buy from? Name one of: " + ", ".join(merchant_ids) + "."})

    blocked = [c for c in CATEGORIES if re.search(rf"\b(?:no|without|exclude|excluding|never buy)\s+(?:any\s+)?{c}\b", lowered)]
    if re.search(r"alcohol[- ]free|\bno\s+(?:beer|wine|liquor|spirits)\b", lowered) and "alcohol" not in blocked:
        blocked.append("alcohol")

    expires_at, problem = _expiry(text, now)
    if problem:
        ambiguities.append({"field": "expires_at", "question": problem})

    approval = _APPROVAL.search(text)
    approval_minor = _minor(approval.group(1)) if approval else None

    if ambiguities:
        return None, ambiguities, "Some rules could not be read from the request; answer the questions, then set the rules below."

    policy = {
        "currency": "HKD",
        "per_order_limit_minor": _minor(order.group(1)),
        "period_limits": periods,
        "allowed_merchant_ids": merchants,
        "blocked_categories": blocked,
        "expires_at": expires_at,
        "approval_above_minor": approval_minor,
    }
    parts = [f"HK${policy['per_order_limit_minor'] / 100:,.2f} per order"]
    parts += [f"HK${p['limit_minor'] / 100:,.2f} per {p['period'].removeprefix('calendar_')} (Hong Kong time)"
              for p in periods]
    parts.append("only from " + ", ".join(merchants))
    parts.append("blocks " + ", ".join(blocked) if blocked else "no blocked categories")
    if approval_minor is not None:
        parts.append(f"asks you before orders over HK${approval_minor / 100:,.2f}")
    parts.append(f"expires {expires_at[:10]} 23:59 HKT")
    summary = "; ".join(parts) + ". Read with fixed rules, not an AI model; check every rule before confirming."
    return policy, [], summary


class DraftService:
    def __init__(self, wallet: Wallet, drafts: InMemoryDrafts):
        self.wallet = wallet
        self.drafts = drafts
        self._keys: dict[tuple[str, str], tuple[str, dict]] = {}
        self._lock = threading.Lock()

    def create(self, actor: Actor, key: str, req: dict) -> tuple[int, dict]:
        request_hash = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
        with self._lock:
            prior = self._keys.get((actor.actor_id, key))
            if prior is not None:
                if prior[0] != request_hash:
                    raise conflict("This Idempotency-Key was already used with a different request.",
                                   code="IDEMPOTENCY_CONFLICT")
                return 201, prior[1]

        now = self.wallet.clock.now()
        merchant_ids = sorted({p["merchant_id"] for p in self.wallet.catalog.listing()["products"]})
        policy, ambiguities, summary = interpret(req["text"], merchant_ids, now)
        draft = {
            "draft_id": f"draft_{uuid.uuid4().hex}",
            "owner_id": actor.actor_id,
            "delegatee_id": req["delegatee_id"],
            "parent_mandate_id": req.get("parent_mandate_id"),
            "proposed_policy": policy,
            "ambiguities": ambiguities,
            "summary": summary,
            "expires_at": iso(now + DRAFT_TTL),
        }
        self.drafts.register(draft["draft_id"], owner_id=actor.actor_id, delegatee_id=draft["delegatee_id"],
                             expires_at=draft["expires_at"], parent_mandate_id=draft["parent_mandate_id"])
        with self._lock:
            self._keys[(actor.actor_id, key)] = (request_hash, draft)
        return 201, draft
