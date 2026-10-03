"""Shopping runs (POST/GET /agent-runs): Jev builds a basket, the wallet quotes it.

A run never authorizes or pays. It picks products for the shopping list at each
merchant the mandate allows, asks the wallet to quote each basket as the
mandate's agent, and keeps the quote that covers the most items at the lowest
total. The user reviews that quote and checks out through the wallet, where
every rule is enforced again.

Runs live in memory: a restarted API forgets them.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass

from mandate.payments.auth import Actor
from mandate.payments.catalog import CatalogError
from mandate.payments.clock import iso
from mandate.payments.errors import ApiError, conflict, invalid, not_found
from mandate.payments.service import Wallet

from .selector import JevSelector, Pick, SelectorError


@dataclass
class _Candidate:
    merchant_id: str
    picks: list[Pick]
    quote: dict | None
    problem: str


class AgentRuns:
    def __init__(self, wallet: Wallet, selector=None, executor: Executor | None = None):
        self.wallet = wallet
        self.selector = selector or JevSelector()
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-run")
        self._runs: dict[str, dict] = {}
        self._owners: dict[str, str] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}  # (actor, key) -> (request hash, run id)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- public

    def start(self, actor: Actor, key: str, req: dict) -> tuple[int, dict]:
        request_hash = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
        with self._lock:
            prior = self._keys.get((actor.actor_id, key))
        if prior is not None:
            if prior[0] != request_hash:
                raise conflict("This Idempotency-Key was already used with a different request.",
                               code="IDEMPOTENCY_CONFLICT")
            return 202, self.get(actor, prior[1])
        if req.get("auto_purchase"):
            raise invalid("auto_purchase is not supported: review the returned quote and check out through the wallet.")
        mandate = self.wallet.get_mandate(actor, req["mandate_id"])  # 404 unless the caller owns it
        if mandate["status"] != "active":
            raise conflict(f"Mandate is {mandate['status']}; a shopping run needs an active mandate.",
                           code="MANDATE_NOT_ACTIVE")

        now = iso(self.wallet.clock.now())
        run = {
            "id": f"run_{uuid.uuid4().hex}", "mandate_id": mandate["id"], "status": "queued",
            "provider": "jev", "model_id": None, "execution_mode": "cloud", "quote_id": None,
            "transaction_id": None, "payment_id": None, "latest_decision_id": None,
            "message": "Queued: Jev will choose products for your list.", "created_at": now, "updated_at": now,
        }
        with self._lock:
            self._runs[run["id"]] = run
            self._owners[run["id"]] = actor.actor_id
            self._keys[(actor.actor_id, key)] = (request_hash, run["id"])
        self.executor.submit(self._execute, run["id"], mandate, req)
        return 202, self.get(actor, run["id"])

    def get(self, actor: Actor, run_id: str) -> dict:
        with self._lock:
            if self._owners.get(run_id) != actor.actor_id:
                raise not_found("Agent run")
            return dict(self._runs[run_id])

    # ------------------------------------------------------------- execution

    def _update(self, run_id: str, **fields) -> None:
        with self._lock:
            self._runs[run_id].update(fields, updated_at=iso(self.wallet.clock.now()))

    def _execute(self, run_id: str, mandate: dict, req: dict) -> None:
        try:
            self._update(run_id, status="running", message="Jev is choosing products for your list.")
            self._run(run_id, mandate, req)
        except Exception as exc:  # a run must end in a terminal state, never hang in "running"
            message = str(exc) if isinstance(exc, SelectorError) else f"Shopping run failed: {exc}"
            self._update(run_id, status="failed", message=message)

    def _run(self, run_id: str, mandate: dict, req: dict) -> None:
        agent = Actor(actor_id=mandate["delegatee_id"], role="agent", owner_id=mandate["owner_id"])
        policy = mandate["policy"]
        items = [{"name": i["name"].strip(), "quantity": i["quantity"], "unit": i.get("unit")}
                 for i in req["shopping_list"]]
        blocked = set(policy.get("blocked_categories", []))

        candidates: list[_Candidate] = []
        for merchant_id in policy["allowed_merchant_ids"]:
            try:
                listing = self.wallet.catalog.listing(merchant_id)
            except CatalogError:
                continue  # the mandate allows a store this catalog does not carry
            contexts = self.wallet.catalog.delivery_context_ids(merchant_id)
            # The agent does not propose what the mandate blocks or what needs a person's check;
            # the wallet still enforces both at checkout.
            products = [p for p in listing["products"] if p["available"] and p["category"] not in blocked
                        and not (blocked and p["category_status"] in ("unknown", "conflicting"))]
            if not contexts or not products:
                continue
            selection = self.selector.choose(items, products, req.get("instruction"))
            self._update(run_id, model_id=selection.model_id)
            candidates.append(self._quote(agent, merchant_id, contexts[0], selection.picks))

        if not candidates:
            self._update(run_id, status="failed",
                         message="None of the stores this mandate allows has a quotable catalog. No basket was built.")
            return
        quoted = [c for c in candidates if c.quote]
        if not quoted:
            reasons = "; ".join(f"{c.merchant_id}: {c.problem}" for c in candidates)
            self._update(run_id, status="failed", message=f"No basket could be quoted ({reasons}).")
            return
        best = min(quoted, key=lambda c: (-sum(p.product_id is not None for p in c.picks), c.quote["total_minor"]))
        self._update(run_id, status="quoted", quote_id=best.quote["id"],
                     message=self._summary(best, policy, len(candidates)))

    def _quote(self, agent: Actor, merchant_id: str, context_id: str, picks: list[Pick]) -> _Candidate:
        lines: dict[str, int] = {}
        for pick in picks:
            if pick.product_id:
                lines[pick.product_id] = lines.get(pick.product_id, 0) + pick.quantity
        if not lines:
            return _Candidate(merchant_id, picks, None, "no list item matched a product")
        try:
            quote = self.wallet.create_quote(agent, {
                "merchant_id": merchant_id, "delivery_context_id": context_id,
                "items": [{"product_id": pid, "quantity": qty} for pid, qty in lines.items()],
            })
        except ApiError as exc:
            return _Candidate(merchant_id, picks, None, exc.message)
        return _Candidate(merchant_id, picks, quote, "")

    @staticmethod
    def _summary(best: _Candidate, policy: dict, stores: int) -> str:
        quote = best.quote
        titles = {line["product_id"]: line["title"] for line in quote["items"]}
        found = [p for p in best.picks if p.product_id]
        missing = [p for p in best.picks if not p.product_id]
        parts = [f"Jev picked {len(found)} of {len(best.picks)} items at {best.merchant_id} "
                 f"for HK${quote['total_minor'] / 100:.2f}"
                 + (f" (best of {stores} stores: most items found, then lowest total)" if stores > 1 else "") + ":"]
        parts.append("; ".join(f"{p.item} → {titles[p.product_id]} ({p.probability:.0%})" for p in found) + ".")
        if missing:
            parts.append("Not added: " + "; ".join(f"{p.item} ({p.reason})" for p in missing) + ".")
            blocked = policy.get("blocked_categories") or []
            if blocked:
                parts.append(f"Products in categories your mandate blocks ({', '.join(blocked)}) were not offered "
                             "to the agent.")
        limit = policy.get("per_order_limit_minor")
        if limit is not None and quote["total_minor"] > limit:
            parts.append(f"This is over your HK${limit / 100:.2f} per-order limit, so the wallet will refuse checkout.")
        return " ".join(parts)
