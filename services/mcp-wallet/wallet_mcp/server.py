"""MCP server that gives an agent the agent-side half of the Mandate wallet.

It is a thin client over the wallet HTTP API in contracts/openapi.json. Every
policy decision, reservation and debit happens in the wallet service; this
process only forwards calls with the agent's bearer token.

Deliberately missing: confirm mandate and revoke (user-only), so an agent that
holds these tools cannot create or widen its own spending authority.

The signed authorization token never enters the model's context. The server
keeps it in memory by transaction ID and attaches it when ``pay`` is called.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

DEFAULT_API_URL = "http://localhost:8000/api/v1"

INSTRUCTIONS = """\
Spend from a family-wallet mandate on behalf of its owner. Amounts are integer HKD cents (29700 = HK$297.00).
Flow: get_budget -> create_quote -> authorize_purchase -> pay. Only quotes from create_quote count; never
invent prices. A refused or requires_review authorization is final for that transaction: tell the user which
rule applied instead of retrying around it. You cannot confirm, widen or revoke a mandate; the owner does that.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class QuoteLine(BaseModel):
    product_id: str = Field(description="Product ID from the catalog.")
    quantity: int = Field(ge=1, le=100)


class WalletAPI:
    """Agent-credential client for the wallet HTTP API."""

    def __init__(self, client: httpx.Client):
        self.client = client
        # transaction_id -> {"quote_id", "token"}; tokens are kept out of tool results.
        self._tokens: dict[str, dict[str, str]] = {}

    @classmethod
    def from_env(cls) -> "WalletAPI":
        token = os.environ.get("MANDATE_AGENT_TOKEN")
        if not token:
            raise SystemExit("Set MANDATE_AGENT_TOKEN to the agent's bearer token (dev server: dev-agent-token).")
        base = os.environ.get("MANDATE_API_URL", DEFAULT_API_URL).rstrip("/") + "/"
        return cls(httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=15))

    def _call(self, method: str, path: str, *, idempotency_key: str | None = None, **kwargs) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            res = self.client.request(method, path.lstrip("/"), headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError(f"Wallet service unreachable: {exc}. Nothing was charged by this call.") from exc
        body: Any
        try:
            body = res.json()
        except ValueError:
            body = None
        if res.is_success:
            return body
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if err:
            retry = " Retrying with the same transaction_id is safe." if err.get("retryable") else ""
            raise ToolError(f"HTTP {res.status_code} {err.get('code')}: {err.get('message')}{retry}")
        raise ToolError(f"HTTP {res.status_code} from wallet service.")

    # --- reads ---------------------------------------------------------------

    def catalog(self, merchant_id: str | None) -> dict:
        return self._call("GET", "/catalog", params={"merchant_id": merchant_id} if merchant_id else None)

    def mandate(self, mandate_id: str) -> dict:
        return self._call("GET", f"/mandates/{mandate_id}")

    def budget(self, mandate_id: str) -> dict:
        return self._call("GET", f"/wallet/{mandate_id}")

    def quote(self, quote_id: str) -> dict:
        return self._call("GET", f"/quotes/{quote_id}")

    def receipt(self, transaction_id: str) -> dict:
        return self._call("GET", f"/payments/{transaction_id}")

    # --- writes --------------------------------------------------------------

    def create_quote(self, merchant_id: str, items: list[QuoteLine], delivery_context_id: str) -> dict:
        return self._call("POST", "/quotes", json={
            "merchant_id": merchant_id,
            "items": [i.model_dump() for i in items],
            "delivery_context_id": delivery_context_id,
        })

    def authorize(self, mandate_id: str, quote_id: str, transaction_id: str) -> dict:
        # The key is derived from the transaction so a retried call replays the stored decision.
        body = self._call("POST", "/authorizations", idempotency_key=f"mcp-authorize-{transaction_id}",
                          json={"transaction_id": transaction_id, "mandate_id": mandate_id, "quote_id": quote_id})
        token = body.pop("authorization_token", None)
        if token:
            self._tokens[transaction_id] = {"quote_id": quote_id, "token": token}
            body["authorization_token"] = "held by the MCP server; call pay with this transaction_id"
        return body

    def pay(self, transaction_id: str) -> dict:
        held = self._tokens.get(transaction_id)
        if not held:
            raise ToolError(
                f"No approved authorization is held for transaction {transaction_id}. Call authorize_purchase "
                "again with the same transaction_id, mandate_id and quote_id; it replays the original decision "
                "and does not reserve twice.")
        return self._call("POST", "/payments", idempotency_key=f"mcp-pay-{transaction_id}", json={
            "transaction_id": transaction_id, "quote_id": held["quote_id"], "authorization_token": held["token"]})

    def cancel(self, reservation_id: str, reason: str | None) -> dict:
        return self._call("POST", f"/reservations/{reservation_id}/cancel",
                          idempotency_key=f"mcp-cancel-{reservation_id}",
                          json={"reason": reason} if reason else {})


def build_server(api: WalletAPI) -> FastMCP:
    mcp = FastMCP("mandate-wallet", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ)
    def get_catalog(merchant_id: str | None = None) -> dict:
        """List products, prices and delivery contexts the wallet can quote, optionally for one merchant."""
        return api.catalog(merchant_id)

    @mcp.tool(annotations=READ)
    def get_mandate(mandate_id: str) -> dict:
        """Read a mandate's status and policy: per-order cap, period budgets, allowed merchants, blocked categories, expiry."""
        return api.mandate(mandate_id)

    @mcp.tool(annotations=READ)
    def get_budget(mandate_id: str) -> dict:
        """Current budgets for a mandate and its ancestors: limit, paid, reserved and available, in HKD cents."""
        return api.budget(mandate_id)

    @mcp.tool(annotations=WRITE)
    def create_quote(merchant_id: str, items: list[QuoteLine], delivery_context_id: str) -> dict:
        """Ask the wallet's trusted adapter to price a one-merchant basket, including delivery fees.

        Returns an immutable quote with total_minor and expiry. Changing quantities, items or delivery
        context needs a new quote.
        """
        return api.create_quote(merchant_id, items, delivery_context_id)

    @mcp.tool(annotations=READ)
    def get_quote(quote_id: str) -> dict:
        """Read an existing quote."""
        return api.quote(quote_id)

    @mcp.tool(annotations=WRITE)
    def authorize_purchase(mandate_id: str, quote_id: str, transaction_id: str | None = None) -> dict:
        """Check a quote against the mandate and, if allowed, reserve the money.

        status is approved (money reserved; call pay next), refused (violations say which rule) or
        requires_review (the owner must act). Omit transaction_id for a new purchase; pass the same one to
        retry safely.
        """
        return api.authorize(mandate_id, quote_id, transaction_id or str(uuid.uuid4()))

    @mcp.tool(annotations=WRITE)
    def pay(transaction_id: str) -> dict:
        """Complete the sandbox payment for an approved transaction and return its receipt.

        The wallet rechecks the mandate and quote before paying. Calling again returns the same receipt
        without a second debit.
        """
        return api.pay(transaction_id)

    @mcp.tool(annotations=READ)
    def get_receipt(transaction_id: str) -> dict:
        """Fetch the receipt for a completed payment."""
        return api.receipt(transaction_id)

    @mcp.tool(annotations=WRITE)
    def cancel_reservation(reservation_id: str, reason: str | None = None) -> dict:
        """Release money reserved by an approved authorization that will not be paid."""
        return api.cancel(reservation_id, reason)

    return mcp


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Mandate wallet MCP server (agent tools only).")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="Port for streamable-http.")
    args = parser.parse_args()
    server = build_server(WalletAPI.from_env())
    server.settings.port = args.port
    server.run(transport=args.transport)
