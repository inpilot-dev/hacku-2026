"""Payment rail adapters: the wallet's ledger states mapped onto card-style hold/capture/void.

    mandate confirmed              -> issue a card (limit = per-order cap, expiry = mandate expiry)
    reservation reserved           -> hold
    reservation paid               -> capture
    reservation cancelled/expired  -> void
    mandate revoked                -> close the card

No real rail is connected. ``TapAndGoSingleUseCardSimulator`` models the
shape of HKT's Tap & Go Single Use Card (a virtual card with a user-set limit
and expiry), but it is a local simulation: no Tap & Go sandbox exists and no
money moves. Every receipt says ``payment_mode: "sandbox"``, and audit
payloads carry ``rail.simulated: true``.

Adapters run inside the ledger's transaction on the same connection, which
is only valid for in-process simulations. A real external rail would need an
outbox and reconciliation instead (docs/mandate-build-plan.md section 2).
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Protocol

from .clock import iso


class RailDeclined(Exception):
    """The rail refused an operation the ledger had already approved: a wallet bug, never a business refusal."""


class PaymentRail(Protocol):
    name: str
    mode: str  # what receipts report; the contract allows only "sandbox" in v0
    simulated: bool

    def issue(self, conn: sqlite3.Connection, mandate: dict, now) -> str: ...
    def hold(self, conn: sqlite3.Connection, reservation: dict, now) -> str: ...
    def capture(self, conn: sqlite3.Connection, reservation: dict, now) -> str: ...
    def void(self, conn: sqlite3.Connection, reservation: dict, now) -> None: ...
    def close(self, conn: sqlite3.Connection, mandate_id: str, now) -> None: ...


class TapAndGoSingleUseCardSimulator:
    """Simulated virtual card per mandate. It enforces its own limit and expiry, as a second line of defence."""

    name = "tap_and_go_single_use_card"
    mode = "sandbox"
    simulated = True

    def issue(self, conn, mandate: dict, now) -> str:
        card_id = f"simcard_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO simulated_cards (id, mandate_id, limit_minor, currency, expires_at, status, issued_at) "
            "VALUES (?,?,?,?,?,'active',?)",
            (card_id, mandate["id"], mandate["policy"]["per_order_limit_minor"], "HKD", mandate["expires_at"], iso(now)),
        )
        return card_id

    def _card(self, conn, mandate_id: str) -> sqlite3.Row:
        card = conn.execute("SELECT * FROM simulated_cards WHERE mandate_id = ?", (mandate_id,)).fetchone()
        if card is None:
            raise RailDeclined(f"No card issued for mandate {mandate_id}.")
        return card

    def hold(self, conn, reservation: dict, now) -> str:
        card = self._card(conn, reservation["mandate_id"])
        if card["status"] != "active":
            raise RailDeclined("Card is closed.")
        if iso(now) >= card["expires_at"]:
            raise RailDeclined("Card has expired.")
        if reservation["amount_minor"] > card["limit_minor"]:
            raise RailDeclined("Amount is over the card limit.")
        hold_id = f"simhold_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO simulated_card_ops (reservation_id, card_id, hold_id, amount_minor, status, updated_at) "
            "VALUES (?,?,?,?,'held',?)",
            (reservation["id"], card["id"], hold_id, reservation["amount_minor"], iso(now)),
        )
        return hold_id

    def _transition(self, conn, reservation: dict, to: str, now) -> str:
        cur = conn.execute(
            "UPDATE simulated_card_ops SET status = ?, updated_at = ? WHERE reservation_id = ? AND status = 'held'",
            (to, iso(now), reservation["id"]),
        )
        if cur.rowcount != 1:
            raise RailDeclined(f"No open hold for reservation {reservation['id']}.")
        return conn.execute("SELECT hold_id FROM simulated_card_ops WHERE reservation_id = ?",
                            (reservation["id"],)).fetchone()[0]

    def capture(self, conn, reservation: dict, now) -> str:
        return self._transition(conn, reservation, "captured", now)

    def void(self, conn, reservation: dict, now) -> None:
        self._transition(conn, reservation, "voided", now)

    def close(self, conn, mandate_id: str, now) -> None:
        conn.execute("UPDATE simulated_cards SET status = 'closed', closed_at = ? WHERE mandate_id = ? AND status = 'active'",
                     (iso(now), mandate_id))


def rail_info(rail: PaymentRail, ref: str | None = None) -> dict:
    """What audit payloads record about the rail; never a credential."""
    return {"name": rail.name, "simulated": rail.simulated, "ref": ref}
