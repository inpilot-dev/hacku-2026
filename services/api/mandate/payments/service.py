"""The wallet ledger: every financial state transition, each in one SQLite write transaction.

Within a transaction the wallet (1) expires overdue reservations, (2) checks
idempotency and transaction uniqueness, (3) evaluates policy against
persisted state, (4) writes the state change and (5) appends the audit event
on the same connection. Nothing commits until all five succeed.

Operations return ``(http_status, body)`` with ``body`` already shaped like
the contract response.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Callable

from mandate.storage.db import Database

from . import policy as rules
from . import risk
from .audit_shim import append_event, sha256_hex, stream_for_owner
from .auth import Actor
from .catalog import Catalog, CatalogError
from .clock import SystemClock, iso, parse, period_bounds
from .drafts import DraftLookup, InMemoryDrafts
from .errors import ApiError, conflict, forbidden, invalid, not_found
from .issuing import CardIssuanceError, CardIssuer, SandboxCardIssuer
from .rails import PaymentRail, RailDeclined, default_rails, rail_info
from .routing import RULE as ROUTE_RULE, RouteBook
from .signing import AUDIENCE, Signer, TokenExpired, TokenInvalid

PAYLOAD_VERSION = 1
# An agent may keep only this many purchases waiting on one mandate's owner, so it cannot
# wear the owner down with a stream of approval requests until one is tapped through.
MAX_PENDING_APPROVALS = 3


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Wallet:
    def __init__(
        self,
        db: Database,
        signer: Signer,
        catalog: Catalog,
        *,
        clock=None,
        draft_lookup: DraftLookup | None = None,
        rails: dict[str, PaymentRail] | None = None,
        issuer: CardIssuer | None = None,
        routes: RouteBook | None = None,
        authorization_ttl_s: int = 120,
        quote_ttl_s: int = 600,
        approval_ttl_s: int = 900,
    ):
        self.db = db
        self.signer = signer
        self.catalog = catalog
        self.clock = clock or SystemClock()
        self.draft_lookup = draft_lookup or InMemoryDrafts()
        self.issuer = issuer or SandboxCardIssuer.ephemeral()
        self.rails = rails or default_rails(self.issuer)
        self.routes = routes or RouteBook.load()
        missing = [name for name in self.routes.rail_names if name not in self.rails]
        if missing:
            raise ValueError(f"Payment routes use rails with no adapter: {missing}")
        self.authorization_ttl = timedelta(seconds=authorization_ttl_s)
        self.quote_ttl = timedelta(seconds=quote_ttl_s)
        self.approval_ttl = timedelta(seconds=approval_ttl_s)

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return self.clock.now()

    def _idempotent(self, actor: Actor, operation: str, key: str, request: dict,
                    op: Callable[[sqlite3.Connection, datetime], tuple[int, dict]]) -> tuple[int, dict, bool]:
        """Run ``op`` once per (actor, operation, key); replay the saved outcome for the same request."""
        request_hash = sha256_hex(request)
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT request_hash, status_code, response_json FROM idempotency_keys "
                "WHERE actor_id = ? AND operation = ? AND key = ?",
                (actor.actor_id, operation, key),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise conflict("This Idempotency-Key was already used with a different request.",
                                   code="IDEMPOTENCY_CONFLICT")
                return row["status_code"], json.loads(row["response_json"]), True
            now = self._now()
            self._expire_overdue(conn, now)
            status, body = op(conn, now)
            conn.execute(
                "INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?,?)",
                (actor.actor_id, operation, key, request_hash, status, json.dumps(body), iso(now)),
            )
        return status, body, False

    def _event(self, conn, owner_id: str, event_type: str, payload: dict, *, actor: Actor | str,
               now: datetime, mandate_id: str | None = None, transaction_id: str | None = None) -> int:
        return append_event(
            conn, stream_for_owner(owner_id), event_type, {"payload_version": PAYLOAD_VERSION, **payload},
            actor_id=actor if isinstance(actor, str) else actor.actor_id,
            mandate_id=mandate_id, transaction_id=transaction_id, occurred_at=iso(now),
        )

    @staticmethod
    def _rail(fn, *args):
        """Rail calls share the ledger transaction; a decline rolls the whole change back."""
        try:
            return fn(*args)
        except RailDeclined as exc:
            raise ApiError(500, "INTERNAL_ERROR", f"Payment rail declined an approved step: {exc}") from None

    def _route_row(self, conn, reservation_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM reservation_routes WHERE reservation_id = ?", (reservation_id,)).fetchone()
        if row is None:
            raise ApiError(500, "INTERNAL_ERROR", f"Reservation {reservation_id} has no payment route.")
        return row

    def _rail_for(self, conn, reservation_id: str) -> PaymentRail:
        return self.rails[self._route_row(conn, reservation_id)["rail"]]

    # cards

    @staticmethod
    def _card_audit(card: dict) -> dict:
        """What audit payloads record about a card: never the number or security code."""
        return {"card_id": card["card_id"], "usage": card["usage"], "network": card["network"],
                "last4": card["last4"], "simulated": True}

    def _ensure_cards(self, conn, chain: list[dict], now: datetime) -> None:
        """Mandates confirmed before the card issuer existed get their card on first use, root first."""
        for m in reversed(chain):
            if self.issuer.mandate_card(conn, m["id"]) is None:
                self.issuer.issue_mandate_card(conn, m, now)

    def _frozen_card_violation(self, conn, chain: list[dict]) -> dict | None:
        for m in chain:
            card = self.issuer.mandate_card(conn, m["id"])
            if card is not None and card["status"] == "frozen":
                whose = "" if m is chain[0] else f" (it funds mandate {chain[0]['id']})"
                return rules.violation("CARD_FROZEN", m, "card",
                                       f"Card •••• {card['last4']} is frozen{whose}; unfreeze it to pay.")
        return None

    # mandates

    @staticmethod
    def _mandate_row(row) -> dict:
        m = dict(row)
        m["policy"] = json.loads(m.pop("policy_json"))
        return m

    def _load_mandate(self, conn, mandate_id: str) -> dict | None:
        row = conn.execute("SELECT * FROM mandates WHERE id = ?", (mandate_id,)).fetchone()
        return self._mandate_row(row) if row else None

    def _chain(self, conn, leaf: dict) -> list[dict]:
        chain, seen = [leaf], {leaf["id"]}
        while chain[-1]["parent_mandate_id"]:
            parent = self._load_mandate(conn, chain[-1]["parent_mandate_id"])
            if parent is None or parent["id"] in seen:
                raise ApiError(500, "INTERNAL_ERROR", "Broken delegation chain.")
            chain.append(parent)
            seen.add(parent["id"])
        return chain

    @staticmethod
    def _can_see_mandate(actor: Actor, m: dict) -> bool:
        if actor.role == "user":
            return m["owner_id"] == actor.actor_id
        if actor.role == "agent":
            # Zero trust: the agent's token names the user it works for; a mandate from anyone else is invisible.
            return m["delegatee_id"] == actor.actor_id and m["owner_id"] == actor.family_id
        return False

    @staticmethod
    def _mandate_out(m: dict, now: datetime) -> dict:
        status = m["status"]
        if status == "active" and parse(m["expires_at"]) <= now:
            status = "expired"
        return {
            "id": m["id"],
            "owner_id": m["owner_id"],
            "delegatee_id": m["delegatee_id"],
            "parent_mandate_id": m["parent_mandate_id"],
            "version": m["version"],
            "status": status,
            "policy": m["policy"],
            "created_at": m["created_at"],
            "revoked_at": m["revoked_at"],
        }

    # budgets

    def _ensure_periods(self, conn, chain: list[dict], now: datetime) -> list[dict]:
        """Current budget period rows for every limit on every mandate in the chain."""
        rows = []
        for m in chain:
            for limit in m["policy"]["period_limits"]:
                start, end = period_bounds(limit["period"], now)
                period_id = f"bp_{m['id']}_{limit['period']}_{start:%Y%m%d}"
                conn.execute(
                    "INSERT OR IGNORE INTO budget_periods (id, mandate_id, period, starts_at, ends_at, limit_minor) "
                    "VALUES (?,?,?,?,?,?)",
                    (period_id, m["id"], limit["period"], iso(start), iso(end), limit["limit_minor"]),
                )
                rows.append(dict(conn.execute("SELECT * FROM budget_periods WHERE id = ?", (period_id,)).fetchone()))
        return rows

    @staticmethod
    def _budget_out(b: dict) -> dict:
        return {
            "mandate_id": b["mandate_id"],
            "period_id": b["id"],
            "period": b["period"],
            "starts_at": b["starts_at"],
            "ends_at": b["ends_at"],
            "currency": "HKD",
            "limit_minor": b["limit_minor"],
            "paid_minor": b["paid_minor"],
            "reserved_minor": b["reserved_minor"],
            "available_minor": b["limit_minor"] - b["paid_minor"] - b["reserved_minor"],
            "version": b["version"],
        }

    def _budgets_by_ids(self, conn, period_ids: list[str]) -> list[dict]:
        out = []
        for pid in period_ids:
            out.append(self._budget_out(dict(conn.execute("SELECT * FROM budget_periods WHERE id = ?", (pid,)).fetchone())))
        return out

    # reservations

    @staticmethod
    def _period_ids(conn, reservation_id: str) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT period_id FROM reservation_periods WHERE reservation_id = ? ORDER BY rowid", (reservation_id,))]

    def _reservation_out(self, conn, r: dict) -> dict:
        return {
            "id": r["id"],
            "transaction_id": r["transaction_id"],
            "mandate_id": r["mandate_id"],
            "mandate_version": r["mandate_version"],
            "quote_id": r["quote_id"],
            "basket_hash": r["basket_hash"],
            "amount_minor": r["amount_minor"],
            "currency": "HKD",
            "status": r["status"],
            "expires_at": r["expires_at"],
            "affected_period_ids": self._period_ids(conn, r["id"]),
        }

    def _close_reservation(self, conn, r: dict, status: str, now: datetime) -> None:
        """Release a reserved amount exactly once. The status guard makes a second release a no-op."""
        cur = conn.execute(
            "UPDATE reservations SET status = ?, closed_at = ? WHERE id = ? AND status = 'reserved'",
            (status, iso(now), r["id"]),
        )
        if cur.rowcount != 1:
            return
        for pid in self._period_ids(conn, r["id"]):
            conn.execute(
                "UPDATE budget_periods SET reserved_minor = reserved_minor - ?, version = version + 1 WHERE id = ?",
                (r["amount_minor"], pid),
            )
        self._rail(self._rail_for(conn, r["id"]).void, conn, r, now)
        r["status"] = status

    def _expire_overdue(self, conn, now: datetime) -> None:
        overdue = conn.execute(
            "SELECT r.*, m.owner_id FROM reservations r JOIN mandates m ON m.id = r.mandate_id "
            "WHERE r.status = 'reserved' AND r.expires_at <= ?",
            (iso(now),),
        ).fetchall()
        for row in overdue:
            r = dict(row)
            self._close_reservation(conn, r, "expired", now)
            self._event(conn, r["owner_id"], "reservation_expired", {
                "reservation_id": r["id"], "released_minor": r["amount_minor"], "expired_at": r["expires_at"],
            }, actor="system_wallet", now=now, mandate_id=r["mandate_id"], transaction_id=r["transaction_id"])
        for row in conn.execute("SELECT * FROM approval_requests WHERE status = 'pending' AND expires_at <= ?",
                                (iso(now),)).fetchall():
            conn.execute("UPDATE approval_requests SET status = 'expired' WHERE id = ? AND status = 'pending'",
                         (row["id"],))
            self._close_review(conn, row["id"], "APPROVAL_EXPIRED",
                               f"Nobody answered the approval request before {row['expires_at']}, so the purchase "
                               f"was not made.", "approval_expired", "system_wallet", now)

    # approvals

    @staticmethod
    def _approval_out(row) -> dict:
        a = dict(row)
        return {
            "id": a["id"], "transaction_id": a["transaction_id"], "mandate_id": a["mandate_id"],
            "quote_id": a["quote_id"], "merchant_id": a["merchant_id"], "basket_hash": a["basket_hash"],
            "amount_minor": a["amount_minor"], "currency": "HKD", "reasons": json.loads(a["reasons_json"]),
            "status": a["status"], "created_at": a["created_at"], "expires_at": a["expires_at"],
            "decided_at": a["decided_at"], "decided_by": a["decided_by"], "note": a["note"],
        }

    def _close_review(self, conn, approval_id: str, code: str, message: str, event_type: str,
                      actor: Actor | str, now: datetime) -> int:
        """Turn a stored requires_review decision into a final refusal, so a retry replays it."""
        a = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
        leaf = self._load_mandate(conn, a["mandate_id"])
        prior = json.loads(conn.execute("SELECT response_json FROM auth_decisions WHERE transaction_id = ?",
                                        (a["transaction_id"],)).fetchone()[0])
        decision_id = _id("dec")
        v = [rules.violation(code, leaf, "approval", message, a["amount_minor"])]
        approval = self._approval_out(a)
        seq = self._event(conn, a["owner_id"], event_type, {
            "approval_id": a["id"], "decision_id": decision_id, "quote_id": a["quote_id"],
            "amount_minor": a["amount_minor"], "status": a["status"], "note": a["note"],
        }, actor=actor, now=now, mandate_id=a["mandate_id"], transaction_id=a["transaction_id"])
        body = {**prior, "status": "refused", "decision_id": decision_id, "message": message, "violations": v,
                "evaluated_at": iso(now), "event_sequence": seq, "approval_request": approval}
        conn.execute("UPDATE auth_decisions SET decision_id = ?, status = 'refused', response_json = ? "
                     "WHERE transaction_id = ?", (decision_id, json.dumps(body), a["transaction_id"]))
        return seq

    # routes and rewards

    @staticmethod
    def _month_start(now: datetime) -> str:
        return iso(period_bounds("calendar_month", now)[0])

    def _spent_by_route(self, conn, owner_id: str, now: datetime) -> dict[str, int]:
        """Spend counted towards each route's monthly reward tiers; refunds count back down."""
        return {row[0]: row[1] for row in conn.execute(
            "SELECT route_id, SUM(spend_minor) FROM reward_ledger WHERE owner_id = ? AND period_start = ? "
            "GROUP BY route_id", (owner_id, self._month_start(now)))}

    @staticmethod
    def _route_summary(option: dict, recommended: str | None = None, with_rule: bool = True) -> dict:
        out = {k: option[k] for k in ("route_id", "label", "network", "rail", "fee_minor", "reward_minor",
                                      "net_minor")}
        if with_rule:
            out.update(rank=option["rank"], recommended_route_id=recommended, rule=ROUTE_RULE,
                       caveats=option["caveats"])
        return out

    # velocity

    @staticmethod
    def _recent_purchases(conn, chain: list[dict], now: datetime) -> dict[str, int]:
        """Reserved or paid purchases in each velocity-limited mandate's subtree inside its window."""
        counts = {}
        for m in chain:
            velocity = m["policy"].get("velocity_limit")
            if not velocity:
                continue
            since = iso(now - timedelta(minutes=velocity["window_minutes"]))
            counts[m["id"]] = conn.execute(
                "WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT c.id FROM mandates c JOIN tree t "
                "ON c.parent_mandate_id = t.id) "
                "SELECT COUNT(*) FROM reservations WHERE mandate_id IN (SELECT id FROM tree) "
                "AND status IN ('reserved', 'paid') AND created_at > ?", (m["id"], since)).fetchone()[0]
        return counts

    # risk

    def _risk(self, conn, chain: list[dict], quote: dict, budgets: list[dict], now: datetime) -> risk.Assessment:
        """Risk score and review reasons for purchases that fit the rules but look unusual for this owner (risk.py)."""
        leaf = chain[0]
        history = [
            risk.PastPurchase(row[0], row[1], {i["product_id"]: i["unit_price_minor"]
                                               for i in json.loads(row[2])["items"]}, parse(row[3]))
            for row in conn.execute(
                "SELECT r.merchant_id, r.amount_minor, q.body_json, r.created_at FROM reservations r "
                "JOIN quotes q ON q.id = r.quote_id JOIN mandates m ON m.id = r.mandate_id "
                "WHERE m.owner_id = ? AND r.status = 'paid' ORDER BY r.created_at", (leaf["owner_id"],))
        ]
        recent = [risk.RecentPurchase(parse(row[0]), row[1]) for row in conn.execute(
            "SELECT created_at, amount_minor FROM reservations WHERE mandate_id = ? AND status IN ('reserved', 'paid')",
            (leaf["id"],))]
        listings = {i["product_id"]: self.catalog.listing_text(i["product_id"]) for i in quote["items"]}
        return risk.assess(leaf, quote, history=history, listings=listings,
                           habits=any(m["policy"].get("risk_review") for m in chain), recent=recent,
                           budgets=[b for b in budgets if b["mandate_id"] == leaf["id"]], now=now)

    # quotes

    def _load_quote(self, conn, quote_id: str, actor: Actor) -> dict | None:
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if row is None or row["family_id"] != actor.family_id:
            return None
        return dict(row)

    def _claims(self, r: dict, merchant_id: str) -> dict:
        return {
            "transaction_id": r["transaction_id"],
            "reservation_id": r["id"],
            "mandate_id": r["mandate_id"],
            "mandate_version": r["mandate_version"],
            "quote_id": r["quote_id"],
            "basket_hash": r["basket_hash"],
            "merchant_id": merchant_id,
            "amount_minor": r["amount_minor"],
            "currency": "HKD",
            "audience": AUDIENCE,
            "issued_at": r["issued_at"],
            "expires_at": r["expires_at"],
            "token_id": r["token_id"],
            "purpose": r["purpose"],
            "payment_route_id": r["payment_route_id"],
        }

    # ---------------------------------------------------------------- mandates

    def confirm_mandate(self, actor: Actor, key: str, req: dict) -> tuple[int, dict]:
        def op(conn, now):
            draft = self.draft_lookup(req["draft_id"])
            if draft is None or draft["owner_id"] != actor.actor_id:
                raise not_found("Draft")
            if parse(draft["expires_at"]) <= now:
                raise conflict("This draft has expired; create a new draft.")
            pol = req["policy"]
            problems = rules.validate_policy(pol, now)
            if problems:
                raise invalid("Policy is invalid.", problems=problems)
            if conn.execute("SELECT 1 FROM mandates WHERE draft_id = ?", (req["draft_id"],)).fetchone():
                raise conflict("This draft was already confirmed.")

            parent_id = draft.get("parent_mandate_id")
            if parent_id:
                parent = self._load_mandate(conn, parent_id)
                if parent is None or parent["owner_id"] != actor.actor_id:
                    raise invalid("Parent mandate is not yours to delegate.", reason_code="PARENT_MANDATE_INVALID")
                for ancestor in self._chain(conn, parent):
                    if rules.mandate_state_violation(ancestor, now):
                        raise invalid("Parent mandate is not active.", reason_code="PARENT_MANDATE_INVALID")
                narrowing = rules.narrowing_problems(pol, parent["policy"])
                if narrowing:
                    raise invalid("A child mandate can only narrow its parent.",
                                  reason_code="POLICY_NOT_NARROWER", problems=narrowing)

            m = {
                "id": _id("m"),
                "owner_id": actor.actor_id,
                "delegatee_id": draft["delegatee_id"],
                "parent_mandate_id": parent_id,
                "draft_id": req["draft_id"],
                "version": 1,
                "status": "active",
                "policy": pol,
                "expires_at": iso(parse(pol["expires_at"])),
                "created_at": iso(now),
                "revoked_at": None,
            }
            conn.execute(
                "INSERT INTO mandates (id, owner_id, delegatee_id, parent_mandate_id, draft_id, version, status, "
                "policy_json, expires_at, created_at, revoked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (m["id"], m["owner_id"], m["delegatee_id"], m["parent_mandate_id"], m["draft_id"], m["version"],
                 m["status"], json.dumps(pol), m["expires_at"], m["created_at"], None),
            )
            # Every rail opens an account bounded by the mandate: per-order limit and expiry.
            accounts = [rail_info(rail, self._rail(rail.issue, conn, m, now)) for rail in self.rails.values()]
            # The mandate's virtual card: the card account every single-use card is issued from.
            card = self.issuer.issue_mandate_card(conn, m, now)
            self._event(conn, actor.actor_id, "mandate_confirmed", {
                "draft_id": req["draft_id"], "delegatee_id": m["delegatee_id"], "parent_mandate_id": parent_id,
                "version": 1, "policy": pol, "rails": accounts, "card": self._card_audit(card),
            }, actor=actor, now=now, mandate_id=m["id"])
            return 201, self._mandate_out(m, now)

        status, body, _ = self._idempotent(actor, "confirmMandate", key, req, op)
        return status, body

    def get_mandate(self, actor: Actor, mandate_id: str) -> dict:
        with self.db.read() as conn:
            m = self._load_mandate(conn, mandate_id)
        if m is None or not self._can_see_mandate(actor, m):
            raise not_found("Mandate")
        return self._mandate_out(m, self._now())

    def revoke_mandate(self, actor: Actor, key: str, mandate_id: str, req: dict) -> tuple[int, dict]:
        def op(conn, now):
            m = self._load_mandate(conn, mandate_id)
            if m is None or m["owner_id"] != actor.actor_id:
                raise not_found("Mandate")
            if m["status"] == "revoked":
                raise conflict("This mandate is already revoked.")
            # The mandate and every descendant lose authority in this transaction.
            ids = [r[0] for r in conn.execute(
                "WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT m.id FROM mandates m JOIN tree t "
                "ON m.parent_mandate_id = t.id) SELECT id FROM tree", (mandate_id,))]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE mandates SET status = 'revoked', version = version + 1, revoked_at = ? "
                f"WHERE id IN ({placeholders}) AND status = 'active'",
                (iso(now), *ids),
            )
            for revoked_id in ids:
                for rail in self.rails.values():
                    self._rail(rail.close, conn, revoked_id, now)
                self.issuer.cancel_mandate_card(conn, revoked_id, now)
            cancelled = []
            for row in conn.execute(
                f"SELECT * FROM reservations WHERE mandate_id IN ({placeholders}) AND status = 'reserved' "
                f"ORDER BY created_at", ids,
            ).fetchall():
                r = dict(row)
                self._close_reservation(conn, r, "cancelled", now)
                cancelled.append(r["id"])
                self._event(conn, m["owner_id"], "reservation_cancelled", {
                    "reservation_id": r["id"], "released_minor": r["amount_minor"], "reason": "mandate_revoked",
                    "revoked_mandate_id": mandate_id,
                }, actor=actor, now=now, mandate_id=r["mandate_id"], transaction_id=r["transaction_id"])
            updated = self._load_mandate(conn, mandate_id)
            seq = self._event(conn, m["owner_id"], "mandate_revoked", {
                "version": updated["version"], "reason": req.get("reason"), "revoked_mandate_ids": ids,
                "cancelled_reservation_ids": cancelled,
            }, actor=actor, now=now, mandate_id=mandate_id)
            return 200, {
                "mandate": self._mandate_out(updated, now),
                "cancelled_reservation_ids": cancelled,
                "event_sequence": seq,
            }

        status, body, _ = self._idempotent(actor, "revokeMandate", key, {"mandate_id": mandate_id, **req}, op)
        return status, body

    # ------------------------------------------------------------------ quotes

    def create_quote(self, actor: Actor, req: dict) -> dict:
        try:
            priced = self.catalog.price(req["merchant_id"], req["items"], req["delivery_context_id"])
        except CatalogError as exc:
            if exc.kind == "not_found":
                raise not_found(str(exc)) from None
            if exc.kind == "unavailable":
                raise conflict(str(exc)) from None
            raise invalid(str(exc), **exc.details) from None
        expected = req.get("expected_revision")
        if expected is not None and expected != priced["revision"]:
            raise conflict("Catalog revision changed; fetch the catalog again.",
                           expected_revision=expected, current_revision=priced["revision"])

        with self.db.write_tx() as conn:
            now = self._now()
            quote = {"id": _id("q"), **priced, "created_at": iso(now), "expires_at": iso(now + self.quote_ttl)}
            quote["basket_hash"] = sha256_hex(quote)
            conn.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (quote["id"], actor.family_id, actor.actor_id, quote["merchant_id"], quote["revision"],
                 quote["total_minor"], quote["basket_hash"],
                 json.dumps({"items": req["items"], "delivery_context_id": req["delivery_context_id"]}),
                 json.dumps(quote), quote["expires_at"], quote["created_at"]),
            )
            self._event(conn, actor.family_id, "quote_created", {
                "quote_id": quote["id"], "merchant_id": quote["merchant_id"], "revision": quote["revision"],
                "total_minor": quote["total_minor"], "basket_hash": quote["basket_hash"],
                "data_mode": quote["data_mode"],
            }, actor=actor, now=now)
        return quote

    def get_quote(self, actor: Actor, quote_id: str) -> dict:
        with self.db.read() as conn:
            q = self._load_quote(conn, quote_id, actor)
        if q is None:
            raise not_found("Quote")
        return json.loads(q["body_json"])

    # ----------------------------------------------------------- authorization

    def authorize(self, actor: Actor, key: str, req: dict, *, owner_present: bool = False) -> tuple[int, dict]:
        """``owner_present``: the mandate's owner is approving this exact purchase themselves right now (web
        purchases), so review reasons are answered by them; hard rules still refuse."""
        def op(conn, now):
            prior = conn.execute("SELECT * FROM auth_decisions WHERE transaction_id = ?",
                                 (req["transaction_id"],)).fetchone()
            approval = None
            if prior is not None:
                if (prior["actor_id"], prior["mandate_id"], prior["quote_id"]) != (
                        actor.actor_id, req["mandate_id"], req["quote_id"]):
                    raise conflict("This transaction_id was already used for a different purchase.",
                                   reason_code="TRANSACTION_CONFLICT")
                approval = conn.execute("SELECT * FROM approval_requests WHERE transaction_id = ?",
                                        (req["transaction_id"],)).fetchone()
                # Only a person's approval reopens a decision; everything else replays it.
                if not (prior["status"] == "requires_review" and approval is not None
                        and approval["status"] == "approved"):
                    body = json.loads(prior["response_json"])
                    if approval is not None and body["status"] != "approved":
                        body["approval_request"] = self._approval_out(approval)
                    return 200, body

            leaf = self._load_mandate(conn, req["mandate_id"])
            if leaf is None or not self._can_see_mandate(actor, leaf):
                raise not_found("Mandate")
            qrow = self._load_quote(conn, req["quote_id"], actor)
            if qrow is None:
                raise not_found("Quote")
            quote = json.loads(qrow["body_json"])

            # An approval waives exactly the review reasons the person saw, for the mandate version they saw.
            waived = frozenset()
            if approval is not None and approval["mandate_version"] == leaf["version"]:
                waived = frozenset((v["code"], v["rule_id"]) for v in json.loads(approval["reasons_json"]))

            chain = self._chain(conn, leaf)
            budgets = self._ensure_periods(conn, chain, now)
            assessment = self._risk(conn, chain, quote, budgets, now)
            ev = rules.evaluate(chain, quote, budgets, now,
                                recent_purchases=self._recent_purchases(conn, chain, now),
                                risk=assessment.reasons, waived=waived)
            if owner_present and actor.role == "user" and leaf["owner_id"] == actor.actor_id:
                ev.review = []
            self._ensure_cards(conn, chain, now)
            frozen = self._frozen_card_violation(conn, chain)
            if frozen:
                ev.hard.append(frozen)
            decision_id = _id("dec")
            base = {
                "decision_id": decision_id,
                "transaction_id": req["transaction_id"],
                "mandate_id": leaf["id"],
                "mandate_version": leaf["version"],
                "rule_ids": ev.rule_ids,
                "evaluated_at": iso(now),
            }
            if assessment.summary() is not None:
                base["risk_assessment"] = assessment.summary()
            snapshot = [{"mandate_id": m["id"], "version": m["version"], "policy": m["policy"]} for m in chain]
            reservation_id = None
            if approval is not None and ev.status == "requires_review":
                if approval["mandate_version"] != leaf["version"]:
                    # The mandate changed after the person approved; their answer no longer covers this purchase.
                    ev.hard.append(rules.violation("MANDATE_VERSION_CHANGED", leaf, "version",
                                                   "The mandate changed after this purchase was approved; "
                                                   "start a new purchase."))
                else:
                    # A reason the person never saw appeared after they approved (e.g. history moved).
                    ev.hard += [{**v, "message": f"New since the approval: {v['message']} Start a new purchase "
                                                 f"so it can be reviewed."} for v in ev.review]
                    ev.review = []
            elif ev.status == "requires_review":
                pending = conn.execute("SELECT COUNT(*) FROM approval_requests WHERE mandate_id = ? AND status = 'pending'",
                                       (leaf["id"],)).fetchone()[0]
                if pending >= MAX_PENDING_APPROVALS:
                    ev.hard.append(rules.violation(
                        "RISK_REVIEW_REQUIRED", leaf, "risk:pending_approvals",
                        f"{pending} purchases are already waiting for {leaf['owner_id']} to answer; the agent "
                        f"must wait for those before asking again."))

            if ev.status == "approved":
                amount = quote["total_minor"]
                options = self.routes.rank(amount, self._spent_by_route(conn, leaf["owner_id"], now))
                eligible = [o for o in options if o["eligible"] and o["rank"] is not None]
                recommended = eligible[0]["route_id"] if eligible else None
                wanted = req.get("payment_route_id") or recommended
                if wanted is None:
                    raise invalid("No payment route has an observed fee. Select a sandbox route explicitly; "
                                  "no lowest-cost recommendation is available.",
                                  reason_code="NO_OBSERVED_FEE_ROUTE")
                chosen = next((o for o in options if o["route_id"] == wanted), None)
                if chosen is None:
                    raise invalid(f"Unknown payment route {wanted!r}.")
                if not chosen["eligible"]:
                    raise invalid(f"Payment route {wanted} cannot take this purchase: {chosen['ineligible_reason']}")
                rail = self.rails[chosen["rail"]]
                route = self._route_summary(chosen, recommended)

                expires = min(
                    [now + self.authorization_ttl, parse(quote["expires_at"])]
                    + [parse(m["expires_at"]) for m in chain]
                    + [parse(b["ends_at"]) for b in budgets]
                )
                r = {
                    "id": _id("res"),
                    "transaction_id": req["transaction_id"],
                    "mandate_id": leaf["id"],
                    "mandate_version": leaf["version"],
                    "quote_id": quote["id"],
                    "basket_hash": quote["basket_hash"],
                    "merchant_id": quote["merchant_id"],
                    "amount_minor": amount,
                    "status": "reserved",
                    "token_id": _id("tok"),
                    "issued_at": iso(now),
                    "expires_at": iso(expires),
                    "created_at": iso(now),
                    "purpose": f"Basket {quote['basket_hash'][:12]} at {quote['merchant_id']} "
                               f"under mandate {leaf['id']} v{leaf['version']}",
                    "payment_route_id": chosen["route_id"],
                }
                conn.execute(
                    "INSERT INTO reservations (id, transaction_id, mandate_id, mandate_version, quote_id, basket_hash, "
                    "merchant_id, amount_minor, status, token_id, issued_at, expires_at, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(r[k] for k in ("id", "transaction_id", "mandate_id", "mandate_version", "quote_id",
                                         "basket_hash", "merchant_id", "amount_minor", "status", "token_id",
                                         "issued_at", "expires_at", "created_at")),
                )
                conn.execute("INSERT INTO reservation_routes VALUES (?,?,?,?)",
                             (r["id"], chosen["route_id"], rail.name, json.dumps({"route": route, "options": options})))
                for b in budgets:
                    conn.execute("INSERT INTO reservation_periods VALUES (?,?)", (r["id"], b["id"]))
                    # The guarded UPDATE re-asserts the budget rule; the table CHECK backs it up.
                    cur = conn.execute(
                        "UPDATE budget_periods SET reserved_minor = reserved_minor + ?, version = version + 1 "
                        "WHERE id = ? AND paid_minor + reserved_minor + ? <= limit_minor",
                        (amount, b["id"], amount),
                    )
                    if cur.rowcount != 1:
                        raise ApiError(500, "INTERNAL_ERROR", "Budget changed during authorization.")
                reservation_id = r["id"]
                credential = self._rail(rail.hold, conn, r, now)
                if approval is not None:
                    conn.execute("UPDATE approval_requests SET status = 'used' WHERE id = ? AND status = 'approved'",
                                 (approval["id"],))
                claims = self._claims(r, quote["merchant_id"])
                seq = self._event(conn, leaf["owner_id"], "authorization_approved", {
                    "decision_id": decision_id, "reservation_id": r["id"], "quote_id": quote["id"],
                    "basket_hash": quote["basket_hash"], "merchant_id": quote["merchant_id"],
                    "amount_minor": amount, "expires_at": r["expires_at"], "token_id": r["token_id"],
                    "rule_ids": ev.rule_ids, "policy_snapshot": snapshot,
                    "approval_id": approval["id"] if approval is not None else None,
                    "payment_route": route, "payment_options": options,
                    "credential": {k: credential[k] for k in ("credential_id", "last4", "merchant_id",
                                                              "amount_minor", "expires_at", "single_use")},
                    "rail": rail_info(rail, credential["credential_id"], rail.funding_for(conn, r["id"])),
                }, actor=actor, now=now, mandate_id=leaf["id"], transaction_id=req["transaction_id"])
                body = {
                    **base,
                    "status": "approved",
                    "message": f"Approved {rules.money(amount)} at {quote['merchant_id']} by {chosen['label']}; "
                               f"funds reserved until {r['expires_at']}.",
                    "event_sequence": seq,
                    "reservation": self._reservation_out(conn, r),
                    "claims": claims,
                    "budgets": self._budgets_by_ids(conn, [b["id"] for b in budgets]),
                    "payment_route": route,
                    "payment_credential": credential,
                }
            else:
                violations = ev.hard + ev.review
                approval_out = None
                if ev.status == "requires_review":
                    # Escalate to the mandate's owner. Unanswered, the request lapses into a refusal.
                    a_expires = min([now + self.approval_ttl, parse(quote["expires_at"])]
                                    + [parse(m["expires_at"]) for m in chain])
                    a = {
                        "id": _id("apr"), "transaction_id": req["transaction_id"], "owner_id": leaf["owner_id"],
                        "agent_id": actor.actor_id, "mandate_id": leaf["id"], "mandate_version": leaf["version"],
                        "quote_id": quote["id"], "merchant_id": quote["merchant_id"],
                        "basket_hash": quote["basket_hash"], "amount_minor": quote["total_minor"],
                        "reasons_json": json.dumps(ev.review), "status": "pending", "created_at": iso(now),
                        "expires_at": iso(a_expires), "decided_at": None, "decided_by": None, "note": None,
                    }
                    approval_out = self._approval_out(a)
                elif approval is not None:
                    approval_out = self._approval_out(approval)
                seq = self._event(conn, leaf["owner_id"], "authorization_refused", {
                    "decision_id": decision_id, "status": ev.status, "quote_id": quote["id"],
                    "basket_hash": quote["basket_hash"], "merchant_id": quote["merchant_id"],
                    "amount_minor": quote["total_minor"], "violations": violations,
                    "rule_ids": ev.rule_ids, "policy_snapshot": snapshot,
                    "approval_request": approval_out,
                }, actor=actor, now=now, mandate_id=leaf["id"], transaction_id=req["transaction_id"])
                body = {
                    **base,
                    "status": ev.status,
                    "message": violations[0]["message"] if ev.status == "refused"
                    else f"Waiting for {leaf['owner_id']} to approve this purchase before "
                         f"{approval_out['expires_at']}.",
                    "event_sequence": seq,
                    "violations": violations,
                    "budgets": [self._budget_out(b) for b in budgets],
                    "approval_request": approval_out,
                }
            if prior is None:
                conn.execute(
                    "INSERT INTO auth_decisions VALUES (?,?,?,?,?,?,?,?,?)",
                    (req["transaction_id"], decision_id, actor.actor_id, leaf["id"], quote["id"], body["status"],
                     reservation_id, json.dumps(body), iso(now)),
                )
            else:
                conn.execute(
                    "UPDATE auth_decisions SET decision_id = ?, status = ?, reservation_id = ?, response_json = ? "
                    "WHERE transaction_id = ?",
                    (decision_id, body["status"], reservation_id, json.dumps(body), req["transaction_id"]),
                )
            if body["status"] == "requires_review":
                conn.execute(
                    "INSERT INTO approval_requests (id, transaction_id, owner_id, agent_id, mandate_id, mandate_version, "
                    "quote_id, merchant_id, basket_hash, amount_minor, reasons_json, status, created_at, expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(a[k] for k in ("id", "transaction_id", "owner_id", "agent_id", "mandate_id",
                                         "mandate_version", "quote_id", "merchant_id", "basket_hash", "amount_minor",
                                         "reasons_json", "status", "created_at", "expires_at")),
                )
            elif approval is not None and body["status"] == "refused":
                conn.execute("UPDATE approval_requests SET status = 'used' WHERE id = ? AND status = 'approved'",
                             (approval["id"],))
            return 200, body

        status, body, _ = self._idempotent(actor, "authorizePurchase", key, req, op)
        if body.get("status") == "approved":
            # Tokens are never stored; Ed25519 re-signing of the same claims yields the same token.
            body = {**body, "authorization_token": self.signer.sign(body["claims"])}
        return status, body

    # ----------------------------------------------------------------- payment

    def pay(self, actor: Actor, key: str, req: dict) -> tuple[int, dict]:
        def op(conn, now):
            row = conn.execute("SELECT * FROM reservations WHERE transaction_id = ?",
                               (req["transaction_id"],)).fetchone()
            if row is None:
                raise not_found("Authorized transaction")
            r = dict(row)
            leaf = self._load_mandate(conn, r["mandate_id"])
            if not self._can_see_mandate(actor, leaf):
                raise not_found("Authorized transaction")

            def refuse(code: str, rule: str, message: str, release: bool, mandate: dict | None = None) -> tuple[int, dict]:
                released = 0
                if release and r["status"] == "reserved":
                    # A refusal after the token verified ends this authorization; free its funds now.
                    self._close_reservation(conn, r, "cancelled", now)
                    released = r["amount_minor"]
                decision_id = _id("dec")
                v = [rules.violation(code, mandate or leaf, rule, message)]
                seq = self._event(conn, leaf["owner_id"], "payment_refused", {
                    "decision_id": decision_id, "reservation_id": r["id"], "quote_id": req["quote_id"],
                    "violations": v, "reservation_status": r["status"], "released_minor": released,
                }, actor=actor, now=now, mandate_id=leaf["id"], transaction_id=r["transaction_id"])
                return 200, {"status": "refused", "decision_id": decision_id, "transaction_id": r["transaction_id"],
                             "violations": v, "message": message, "event_sequence": seq}

            # Signature first: an unverified token must not touch the reservation.
            try:
                claims = self.signer.verify(req["authorization_token"], now, check_expiry=False)
            except TokenInvalid:
                return refuse("AUTHORIZATION_INVALID", "authorization", "The authorization token is not valid.", False)
            bound = (claims.get("transaction_id") == r["transaction_id"] and claims.get("reservation_id") == r["id"]
                     and claims.get("token_id") == r["token_id"])
            if not bound:
                return refuse("AUTHORIZATION_INVALID", "authorization",
                              "The authorization token belongs to a different transaction.", False)

            paid = conn.execute("SELECT * FROM payments WHERE transaction_id = ?", (r["transaction_id"],)).fetchone()
            if paid is not None:
                if req["quote_id"] != r["quote_id"]:
                    raise conflict("This transaction was already paid for a different quote.",
                                   reason_code="TRANSACTION_CONFLICT")
                return 200, {"status": "completed", "decision_id": paid["decision_id"],
                             "receipt": json.loads(paid["receipt_json"]), "replayed": True,
                             "event_sequence": paid["event_sequence"]}

            if req["quote_id"] != r["quote_id"] or claims.get("quote_id") != r["quote_id"]:
                return refuse("QUOTE_CHANGED", "quote", "The quote differs from the one that was authorized.", True)
            try:
                self.signer.verify(req["authorization_token"], now)
            except TokenExpired:
                return refuse("AUTHORIZATION_EXPIRED", "authorization",
                              f"The authorization expired at {r['expires_at']}.", True)
            # Current authority first, so a revoked mandate is reported as revoked rather
            # than as the reservation that revocation cancelled.
            for m in self._chain(conn, leaf):
                state = rules.mandate_state_violation(m, now)
                if state:
                    return refuse(state["code"], "status", state["message"], True, m)
            if r["status"] == "cancelled":
                return refuse("RESERVATION_CANCELLED", "reservation", "The reservation was cancelled.", False)
            if r["status"] == "expired":
                return refuse("RESERVATION_EXPIRED", "reservation", f"The reservation expired at {r['expires_at']}.", False)
            if leaf["version"] != r["mandate_version"]:
                return refuse("MANDATE_VERSION_CHANGED", "version",
                              "The mandate changed after this purchase was authorized.", True)
            frozen = self._frozen_card_violation(conn, self._chain(conn, leaf))
            if frozen:
                # The checkout presents the card and the issuer declines it; the decline stays on the card's record.
                held = conn.execute("SELECT credential_id FROM rail_payments WHERE reservation_id = ?",
                                    (r["id"],)).fetchone()
                card = (self.issuer.card(conn, held["credential_id"]) if held else None) \
                    or self.issuer.mandate_card(conn, leaf["id"])
                self.issuer.record_decline(conn, card["card_id"], "card_frozen", merchant_id=r["merchant_id"],
                                           amount_minor=r["amount_minor"], currency="HKD", now=now,
                                           reservation_id=r["id"])
                mandate = next(m for m in self._chain(conn, leaf) if m["id"] == frozen["mandate_id"])
                return refuse("CARD_FROZEN", "card", frozen["message"], True, mandate)

            qrow = conn.execute("SELECT * FROM quotes WHERE id = ?", (r["quote_id"],)).fetchone()
            quote = json.loads(qrow["body_json"])
            if quote["basket_hash"] != r["basket_hash"] or quote["total_minor"] != r["amount_minor"]:
                return refuse("QUOTE_CHANGED", "quote", "The stored quote no longer matches the reservation.", True)
            # The trusted adapter re-prices the basket: a changed fee or catalog revision needs a new quote.
            # A web checkout has no catalog entry; its total was re-read from the page before the card was issued,
            # and the single-use card cannot be charged more than it.
            if quote.get("source") != rules.WEB_CHECKOUT:
                original = json.loads(qrow["request_json"])
                try:
                    fresh = self.catalog.price(quote["merchant_id"], original["items"],
                                               original["delivery_context_id"])
                except CatalogError as exc:
                    return refuse("QUOTE_CHANGED", "quote", f"The basket can no longer be priced: {exc}", True)
                fields = ("revision", "items", "charges", "subtotal_minor", "total_minor")
                if any(fresh[f] != quote[f] for f in fields):
                    return refuse("QUOTE_CHANGED", "quote",
                                  f"The shop's price changed from {rules.money(quote['total_minor'])} to "
                                  f"{rules.money(fresh['total_minor'])}; a new quote is needed.", True)

            # Commit: reserved -> paid in every period, exactly once.
            cur = conn.execute("UPDATE reservations SET status = 'paid', closed_at = ? WHERE id = ? AND status = 'reserved'",
                               (iso(now), r["id"]))
            if cur.rowcount != 1:
                raise ApiError(500, "INTERNAL_ERROR", "Reservation changed during payment.")
            for pid in self._period_ids(conn, r["id"]):
                conn.execute(
                    "UPDATE budget_periods SET reserved_minor = reserved_minor - ?, paid_minor = paid_minor + ?, "
                    "version = version + 1 WHERE id = ?",
                    (r["amount_minor"], r["amount_minor"], pid),
                )
            route_row = self._route_row(conn, r["id"])
            rail = self.rails[route_row["rail"]]
            capture_ref = self._rail(rail.capture, conn, r, now)
            # Price the reward at capture: an earlier payment this month may have used up a tier.
            route = self.routes.get(route_row["route_id"])
            if route is not None:
                option = self.routes.option(route, r["amount_minor"],
                                            self._spent_by_route(conn, leaf["owner_id"], now).get(route["id"], 0))
            else:  # the route was removed from the route book after authorization
                option = {**json.loads(route_row["choice_json"])["route"], "reward_minor": 0}
                option["net_minor"] = r["amount_minor"] + option["fee_minor"]
            conn.execute(
                "INSERT INTO reward_ledger (owner_id, route_id, transaction_id, kind, spend_minor, reward_minor, "
                "period_start, created_at) VALUES (?,?,?,'earn',?,?,?,?)",
                (leaf["owner_id"], route_row["route_id"], r["transaction_id"], r["amount_minor"],
                 option["reward_minor"], self._month_start(now), iso(now)))
            decision_id = _id("dec")
            receipt = {
                "id": _id("rcpt"),
                "transaction_id": r["transaction_id"],
                "reservation_id": r["id"],
                "mandate_id": leaf["id"],
                "quote_id": r["quote_id"],
                "merchant_id": r["merchant_id"],
                "basket_hash": r["basket_hash"],
                "amount_minor": r["amount_minor"],
                "currency": "HKD",
                "payment_mode": rail.mode,
                "status": "paid",
                "paid_at": iso(now),
                "payment_route": self._route_summary(option, with_rule=False),
            }
            seq = self._event(conn, leaf["owner_id"], "payment_completed", {
                "decision_id": decision_id, "receipt": receipt, "mandate_version": leaf["version"],
                "rail": rail_info(rail, capture_ref, rail.funding_for(conn, r["id"])),
            }, actor=actor, now=now, mandate_id=leaf["id"], transaction_id=r["transaction_id"])
            conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?)",
                         (receipt["id"], r["transaction_id"], r["id"], decision_id, seq, json.dumps(receipt),
                          receipt["paid_at"]))
            return 200, {"status": "completed", "decision_id": decision_id, "receipt": receipt,
                         "replayed": False, "event_sequence": seq}

        status, body, replayed = self._idempotent(actor, "commitPayment", key, req, op)
        if replayed and body.get("status") == "completed":
            body = {**body, "replayed": True}
        return status, body

    def get_payment(self, actor: Actor, transaction_id: str) -> dict:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT p.receipt_json, m.owner_id, m.delegatee_id FROM payments p "
                "JOIN reservations r ON r.id = p.reservation_id JOIN mandates m ON m.id = r.mandate_id "
                "WHERE p.transaction_id = ?", (transaction_id,)).fetchone()
        if row is None or not self._can_see_mandate(actor, {"owner_id": row["owner_id"], "delegatee_id": row["delegatee_id"]}):
            raise not_found("Payment")
        return json.loads(row["receipt_json"])

    # ----------------------------------------------------------- web purchases
    #
    # A one-time web purchase (agent/purchase) pays with a single-use card from the wallet. The owner approves the
    # exact total read from the shop's checkout page; the wallet turns that total into a quote, authorizes it
    # against the owner's allowance (every hard rule applies) and holds a card locked to the shop and the total.

    def _web_mandate(self, conn, actor: Actor, now: datetime) -> dict | None:
        """The owner's newest active allowance that opted in to web purchases, with every ancestor opted in too."""
        for row in conn.execute("SELECT * FROM mandates WHERE owner_id = ? AND status = 'active' "
                                "ORDER BY created_at DESC", (actor.actor_id,)).fetchall():
            m = self._mandate_row(row)
            if parse(m["expires_at"]) > now and all(c["policy"].get("web_purchases") for c in self._chain(conn, m)):
                return m
        return None

    def _web_reservation(self, conn, actor: Actor, transaction_id: str) -> dict:
        row = conn.execute("SELECT r.*, m.owner_id, m.delegatee_id FROM reservations r "
                           "JOIN mandates m ON m.id = r.mandate_id WHERE r.transaction_id = ?",
                           (transaction_id,)).fetchone()
        if row is None or not self._can_see_mandate(actor, dict(row)):
            raise not_found("Web purchase")
        return dict(row)

    def authorize_web_purchase(self, actor: Actor, *, purchase_id: str, merchant_id: str, amount_minor: int,
                               title: str, url: str) -> dict:
        """Authorize a web purchase its owner is approving now; on approval a single-use card is held for it.

        Returns the authorization body: ``approved`` carries ``payment_credential`` (the card's id and last4) and
        ``authorization_token``; ``refused`` carries ``violations``.
        """
        if actor.role != "user":
            raise forbidden("Only the allowance's owner can approve a web purchase.")
        with self.db.write_tx() as conn:
            now = self._now()
            leaf = self._web_mandate(conn, actor, now)
            if leaf is None:
                raise conflict("No allowance allows web purchases. Turn them on for an allowance in the wallet.",
                               reason_code="WEB_PURCHASES_OFF")
            line = {"product_id": f"web:{url}", "title": title, "quantity": 1, "unit_price_minor": amount_minor,
                    "line_total_minor": amount_minor, "category": "unknown", "category_status": "unknown",
                    "evidence_ids": []}
            quote = {"id": _id("q"), "merchant_id": merchant_id, "revision": rules.WEB_CHECKOUT, "currency": "HKD",
                     "items": [line], "subtotal_minor": amount_minor, "charges": [], "total_minor": amount_minor,
                     "delivery_context_id": None, "data_mode": "observed_checkout", "evidence_ids": [],
                     "source": rules.WEB_CHECKOUT, "checkout_url": url,
                     "created_at": iso(now), "expires_at": iso(now + self.quote_ttl)}
            quote["basket_hash"] = sha256_hex(quote)
            conn.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (quote["id"], actor.family_id, actor.actor_id, merchant_id, quote["revision"], amount_minor,
                 quote["basket_hash"], json.dumps({"source": rules.WEB_CHECKOUT, "checkout_url": url}),
                 json.dumps(quote), quote["expires_at"], quote["created_at"]),
            )
            self._event(conn, actor.family_id, "quote_created", {
                "quote_id": quote["id"], "merchant_id": merchant_id, "revision": quote["revision"],
                "total_minor": amount_minor, "basket_hash": quote["basket_hash"], "data_mode": quote["data_mode"],
            }, actor=actor, now=now)
            options = self.routes.rank(amount_minor, self._spent_by_route(conn, leaf["owner_id"], now))
        # A shop's payment form takes a card number, so only routes that issue a card can pay.
        cards = [o for o in options if getattr(self.rails[o["rail"]], "issuer", None) is not None]
        eligible = [o for o in cards if o["eligible"]]
        if not eligible:
            reasons = "; ".join(f"{o['label']}: {o['ineligible_reason']}" for o in cards)
            raise conflict(f"No card can pay {rules.money(amount_minor)} ({reasons}).", reason_code="NO_CARD_ROUTE")
        # Owner-approved sandbox checkout still needs a card. Without observed fees,
        # choose by stable identity, never by assumed cost or savings.
        ranked_cards = [o for o in eligible if o["rank"] is not None]
        chosen_card = ranked_cards[0] if ranked_cards else min(eligible, key=lambda o: o["route_id"])
        _, body = self.authorize(actor, f"web-authorize:{purchase_id}", {
            "transaction_id": f"web_{purchase_id}", "mandate_id": leaf["id"], "quote_id": quote["id"],
            "payment_route_id": chosen_card["route_id"],
        }, owner_present=True)
        return body

    def web_card_details(self, actor: Actor, transaction_id: str) -> dict:
        """The held card's number, expiry and CVV, only while its reservation is open. Never returned by the API."""
        with self.db.read() as conn:
            r = self._web_reservation(conn, actor, transaction_id)
            if r["status"] != "reserved" or iso(self._now()) >= r["expires_at"]:
                raise conflict(f"The card's authorization is {r['status']}.", reservation_status=r["status"])
            held = conn.execute("SELECT credential_id FROM rail_payments WHERE reservation_id = ?",
                                (r["id"],)).fetchone()
            try:
                return self.issuer.checkout_details(conn, held["credential_id"])
            except CardIssuanceError as exc:
                raise conflict(str(exc)) from None

    def settle_web_purchase(self, actor: Actor, transaction_id: str, authorization_token: str) -> dict:
        """The shop confirmed the order: capture the single-use card and record the payment and receipt."""
        with self.db.read() as conn:
            r = self._web_reservation(conn, actor, transaction_id)
        _, body = self.pay(actor, f"web-pay:{transaction_id}", {
            "transaction_id": transaction_id, "quote_id": r["quote_id"], "authorization_token": authorization_token,
        })
        return body

    def release_web_purchase(self, actor: Actor, transaction_id: str, reason: str) -> None:
        """Nothing was ordered: cancel the card and release the reserved amount. A closed reservation is left as is."""
        with self.db.read() as conn:
            r = self._web_reservation(conn, actor, transaction_id)
        if r["status"] == "reserved":
            self.cancel_reservation(actor, f"web-release:{transaction_id}", r["id"], {"reason": reason})

    # ------------------------------------------------------------ cancellation

    def cancel_reservation(self, actor: Actor, key: str, reservation_id: str, req: dict) -> tuple[int, dict]:
        def op(conn, now):
            row = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            if row is None:
                raise not_found("Reservation")
            r = dict(row)
            leaf = self._load_mandate(conn, r["mandate_id"])
            if not self._can_see_mandate(actor, leaf):
                raise not_found("Reservation")
            if r["status"] != "reserved":
                raise conflict(f"Reservation is already {r['status']}.", reservation_status=r["status"])
            self._close_reservation(conn, r, "cancelled", now)
            seq = self._event(conn, leaf["owner_id"], "reservation_cancelled", {
                "reservation_id": r["id"], "released_minor": r["amount_minor"], "reason": req.get("reason"),
            }, actor=actor, now=now, mandate_id=leaf["id"], transaction_id=r["transaction_id"])
            return 200, {"reservation": self._reservation_out(conn, r), "released_minor": r["amount_minor"],
                         "event_sequence": seq}

        status, body, _ = self._idempotent(actor, "cancelReservation", key,
                                           {"reservation_id": reservation_id, **req}, op)
        return status, body

    # ------------------------------------------------------------------ wallet

    def budget(self, actor: Actor, mandate_id: str) -> dict:
        with self.db.write_tx() as conn:
            now = self._now()
            leaf = self._load_mandate(conn, mandate_id)
            if leaf is None or not self._can_see_mandate(actor, leaf):
                raise not_found("Mandate")
            self._expire_overdue(conn, now)
            budgets = self._ensure_periods(conn, self._chain(conn, leaf), now)
            return {"mandate_id": mandate_id, "applicable_budgets": [self._budget_out(b) for b in budgets],
                    "server_time": iso(now)}

    # ---------------------------------------------------------- payment routes

    def payment_options(self, actor: Actor, quote_id: str) -> dict:
        """Every route for a quote, with fees, rewards, net cost and rank under the stated rule."""
        with self.db.read() as conn:
            q = self._load_quote(conn, quote_id, actor)
            if q is None:
                raise not_found("Quote")
            now = self._now()
            options = self.routes.rank(q["total_minor"], self._spent_by_route(conn, actor.family_id, now))
        evidence_ids = list(dict.fromkeys(i for o in options for i in o["evidence_ids"]))
        eligible = [o for o in options if o["eligible"] and o["rank"] is not None]
        return {
            "quote_id": quote_id,
            "currency": "HKD",
            "total_minor": q["total_minor"],
            "rule": ROUTE_RULE,
            "recommended_route_id": eligible[0]["route_id"] if eligible else None,
            "options": options,
            "evidence": self.routes.evidence(evidence_ids),
            "evaluated_at": iso(now),
        }

    # --------------------------------------------------------------- approvals

    def _visible_approval(self, actor: Actor, row) -> bool:
        if actor.role == "user":
            return row["owner_id"] == actor.actor_id
        return actor.role == "agent" and row["agent_id"] == actor.actor_id

    def list_approvals(self, actor: Actor, status: str | None = None) -> dict:
        with self.db.write_tx() as conn:
            now = self._now()
            self._expire_overdue(conn, now)
            column = "owner_id" if actor.role == "user" else "agent_id"
            sql = f"SELECT * FROM approval_requests WHERE {column} = ?"
            args: list = [actor.actor_id]
            if status:
                sql += " AND status = ?"
                args.append(status)
            rows = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC", args).fetchall()
        return {"approvals": [self._approval_out(r) for r in rows], "server_time": iso(now)}

    def get_approval(self, actor: Actor, approval_id: str) -> dict:
        with self.db.write_tx() as conn:
            self._expire_overdue(conn, self._now())
            row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
        if row is None or not self._visible_approval(actor, row):
            raise not_found("Approval request")
        return self._approval_out(row)

    def decide_approval(self, actor: Actor, key: str, approval_id: str, approve: bool, req: dict) -> tuple[int, dict]:
        """The mandate's owner answers a pending request. Agents can never reach this."""
        def op(conn, now):
            row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
            if row is None or row["owner_id"] != actor.actor_id:
                raise not_found("Approval request")
            if row["status"] != "pending":
                raise conflict(f"This approval request is already {row['status']}.", approval_status=row["status"])
            if approve:
                leaf = self._load_mandate(conn, row["mandate_id"])
                for m in self._chain(conn, leaf):
                    if rules.mandate_state_violation(m, now):
                        raise conflict("The mandate is no longer active, so this purchase cannot be approved.")
            status = "approved" if approve else "denied"
            conn.execute("UPDATE approval_requests SET status = ?, decided_at = ?, decided_by = ?, note = ? "
                         "WHERE id = ? AND status = 'pending'",
                         (status, iso(now), actor.actor_id, req.get("note"), approval_id))
            if approve:
                seq = self._event(conn, actor.actor_id, "approval_granted", {
                    "approval_id": approval_id, "quote_id": row["quote_id"], "basket_hash": row["basket_hash"],
                    "amount_minor": row["amount_minor"], "waived_codes": [v["code"] for v in
                                                                         json.loads(row["reasons_json"])],
                    "note": req.get("note"),
                }, actor=actor, now=now, mandate_id=row["mandate_id"], transaction_id=row["transaction_id"])
            else:
                seq = self._close_review(conn, approval_id, "APPROVAL_DENIED",
                                         f"{actor.actor_id} declined this purchase.", "approval_denied", actor, now)
            updated = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
            return 200, {"approval": self._approval_out(updated), "event_sequence": seq}

        operation = "approvePurchase" if approve else "denyPurchase"
        status, body, _ = self._idempotent(actor, operation, key, {"approval_id": approval_id, **req}, op)
        return status, body

    # ----------------------------------------------------------------- refunds

    def refund(self, actor: Actor, key: str, transaction_id: str, req: dict) -> tuple[int, dict]:
        """Full refund of a paid purchase: money back on the rail, budget back, and the reward taken back."""
        def op(conn, now):
            row = conn.execute(
                "SELECT p.*, m.owner_id FROM payments p JOIN reservations r ON r.id = p.reservation_id "
                "JOIN mandates m ON m.id = r.mandate_id WHERE p.transaction_id = ?", (transaction_id,)).fetchone()
            if row is None or row["owner_id"] != actor.actor_id:
                raise not_found("Payment")
            if conn.execute("SELECT 1 FROM refunds WHERE transaction_id = ?", (transaction_id,)).fetchone():
                raise conflict("This payment was already refunded.")
            r = dict(conn.execute("SELECT * FROM reservations WHERE id = ?", (row["reservation_id"],)).fetchone())
            amount = r["amount_minor"]
            route_row = self._route_row(conn, r["id"])
            rail = self.rails[route_row["rail"]]
            rail_ref = self._rail(rail.refund, conn, r, amount, now)

            period_ids = self._period_ids(conn, r["id"])
            for pid in period_ids:
                conn.execute("UPDATE budget_periods SET paid_minor = paid_minor - ?, version = version + 1 "
                             "WHERE id = ?", (amount, pid))

            earned = conn.execute("SELECT * FROM reward_ledger WHERE transaction_id = ? AND kind = 'earn'",
                                  (transaction_id,)).fetchone()
            reversed_minor = 0
            if earned is not None:
                reversed_minor = earned["reward_minor"]
                # Same period as the earn entry, so the monthly tier position moves back too.
                conn.execute(
                    "INSERT INTO reward_ledger (owner_id, route_id, transaction_id, kind, spend_minor, reward_minor, "
                    "period_start, created_at) VALUES (?,?,?,'reverse',?,?,?,?)",
                    (earned["owner_id"], earned["route_id"], transaction_id, -earned["spend_minor"],
                     -reversed_minor, earned["period_start"], iso(now)))

            refund = {
                "id": _id("rf"),
                "transaction_id": transaction_id,
                "amount_minor": amount,
                "currency": "HKD",
                "reward_reversed_minor": reversed_minor,
                "payment_route_id": route_row["route_id"],
                "reason": req.get("reason"),
                "status": "refunded",
                "refunded_at": iso(now),
            }
            seq = self._event(conn, row["owner_id"], "payment_refunded", {
                "refund": refund, "reservation_id": r["id"], "released_paid_minor": amount,
                "affected_period_ids": period_ids, "rail": rail_info(rail, rail_ref, rail.funding_for(conn, r["id"])),
            }, actor=actor, now=now, mandate_id=r["mandate_id"], transaction_id=transaction_id)
            body = {"refund": refund, "budgets": self._budgets_by_ids(conn, period_ids), "event_sequence": seq}
            conn.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (refund["id"], transaction_id, amount, reversed_minor, route_row["route_id"], rail_ref,
                          req.get("reason"), actor.actor_id, refund["refunded_at"], json.dumps(body)))
            return 200, body

        status, body, _ = self._idempotent(actor, "refundPayment", key, {"transaction_id": transaction_id, **req}, op)
        return status, body

    # ------------------------------------------------------------------- cards

    def _card_mandate(self, conn, actor: Actor, mandate_id: str, *, owner_only: bool) -> tuple[dict, dict]:
        m = self._load_mandate(conn, mandate_id)
        visible = m is not None and (m["owner_id"] == actor.actor_id if owner_only else self._can_see_mandate(actor, m))
        if not visible:
            raise not_found("Mandate")
        card = self.issuer.mandate_card(conn, mandate_id)
        if card is None:
            raise not_found("Card")
        return m, card

    @staticmethod
    def _card_out(card: dict) -> dict:
        out = {k: card[k] for k in ("card_id", "mandate_id", "parent_card_id", "usage", "network", "last4",
                                    "exp_month", "exp_year", "status", "controls", "issued_at",
                                    "status_changed_at")}
        return {**out, "payment_mode": "sandbox"}

    def _card_view(self, conn, card: dict) -> dict:
        out = self._card_out(card)
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM virtual_cards WHERE parent_card_id = ? AND usage = 'single_use' "
            "GROUP BY status", (card["card_id"],)).fetchall())
        out["single_use_cards"] = {s: counts.get(s, 0) for s in ("active", "used", "cancelled")}
        return out

    def get_card(self, actor: Actor, mandate_id: str) -> dict:
        with self.db.read() as conn:
            _, card = self._card_mandate(conn, actor, mandate_id, owner_only=False)
            return self._card_view(conn, card)

    def _set_card_frozen(self, actor: Actor, key: str, mandate_id: str, req: dict, freeze: bool) -> tuple[int, dict]:
        def op(conn, now):
            m, card = self._card_mandate(conn, actor, mandate_id, owner_only=True)
            if card["status"] == "cancelled" or m["status"] == "revoked":
                raise conflict("This card was cancelled when its mandate was revoked.")
            if card["status"] == ("frozen" if freeze else "active"):
                raise conflict(f"This card is already {'frozen' if freeze else 'active'}.")
            self.issuer.set_status(conn, card["card_id"], "frozen" if freeze else "active", now)
            card = self.issuer.card(conn, card["card_id"])
            seq = self._event(conn, m["owner_id"], "card_frozen" if freeze else "card_unfrozen", {
                "card": self._card_audit(card), "reason": req.get("reason"),
            }, actor=actor, now=now, mandate_id=mandate_id)
            return 200, {"card": self._card_view(conn, card), "event_sequence": seq}

        operation = "freezeCard" if freeze else "unfreezeCard"
        status, body, _ = self._idempotent(actor, operation, key, {"mandate_id": mandate_id, **req}, op)
        return status, body

    def freeze_card(self, actor: Actor, key: str, mandate_id: str, req: dict) -> tuple[int, dict]:
        """Owner only. Reversible: every card under it declines and the wallet refuses new purchases."""
        return self._set_card_frozen(actor, key, mandate_id, req, True)

    def unfreeze_card(self, actor: Actor, key: str, mandate_id: str, req: dict) -> tuple[int, dict]:
        return self._set_card_frozen(actor, key, mandate_id, req, False)

    def card_authorizations(self, actor: Actor, mandate_id: str) -> dict:
        with self.db.read() as conn:
            _, card = self._card_mandate(conn, actor, mandate_id, owner_only=False)
            return {"card_id": card["card_id"], "authorizations": self.issuer.authorizations(conn, [mandate_id])}
