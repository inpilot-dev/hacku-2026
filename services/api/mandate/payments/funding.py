"""Simulated funding: the owner's own money behind each single-use card.

The single-use card pays the shop; the owner's funding source pays for the card:

    purchase reserved   -> hold the exact authorized amount on the owner's funding source
    purchase paid       -> once the issuer approves the card, capture what the shop charged
                           (never more than was held)
    purchase cancelled  -> release the hold (cancel, expiry, freeze, revoke)
    payment refunded    -> refund the captured amount back to the funding source

The hold is placed before the card can be used, so the wallet never fronts money it has not
secured from the owner. Captured is at most held and refunded at most captured, enforced by
guarded updates and the table's CHECK constraints.

This is a local simulation: no balance is checked and no money moves. A real funding source
(a manual-capture card PaymentIntent, a stored-value debit) would sit behind the same methods,
with its result reconciled outside the ledger transaction.
"""

from __future__ import annotations

import sqlite3
import uuid

from .clock import iso


class FundingDeclined(Exception):
    """The funding source refused a step the ledger had already approved: a wallet bug, never a business refusal."""


class SimulatedFundingSource:
    simulated = True

    def __init__(self, kind: str, label: str):
        self.kind = kind  # e.g. "stored_value" or "card_authorization"
        self.label = label

    def hold(self, conn: sqlite3.Connection, reservation: dict, card_id: str | None, now) -> dict:
        hold_id = f"fhold_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO funding_holds (id, reservation_id, card_id, source_kind, source_label, amount_minor, "
            "currency, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'HKD','held',?,?)",
            (hold_id, reservation["id"], card_id, self.kind, self.label, reservation["amount_minor"],
             iso(now), iso(now)))
        return self.get(conn, reservation["id"])

    def capture(self, conn: sqlite3.Connection, reservation_id: str, amount_minor: int, now) -> None:
        cur = conn.execute(
            "UPDATE funding_holds SET status = 'captured', captured_minor = ?, updated_at = ? "
            "WHERE reservation_id = ? AND status = 'held' AND ? <= amount_minor",
            (amount_minor, iso(now), reservation_id, amount_minor))
        if cur.rowcount != 1:
            raise FundingDeclined(f"No open funding hold of at least {amount_minor} for reservation {reservation_id}.")

    def release(self, conn: sqlite3.Connection, reservation_id: str, now) -> None:
        cur = conn.execute("UPDATE funding_holds SET status = 'released', updated_at = ? "
                           "WHERE reservation_id = ? AND status = 'held'", (iso(now), reservation_id))
        if cur.rowcount != 1:
            raise FundingDeclined(f"No open funding hold for reservation {reservation_id}.")

    def refund(self, conn: sqlite3.Connection, reservation_id: str, amount_minor: int, now) -> None:
        cur = conn.execute(
            "UPDATE funding_holds SET status = 'refunded', refunded_minor = ?, updated_at = ? "
            "WHERE reservation_id = ? AND status = 'captured' AND ? <= captured_minor",
            (amount_minor, iso(now), reservation_id, amount_minor))
        if cur.rowcount != 1:
            raise FundingDeclined(f"No captured funding of at least {amount_minor} for reservation {reservation_id}.")

    @staticmethod
    def get(conn: sqlite3.Connection, reservation_id: str) -> dict | None:
        row = conn.execute("SELECT * FROM funding_holds WHERE reservation_id = ?", (reservation_id,)).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in ("id", "reservation_id", "card_id", "source_kind", "source_label", "amount_minor",
                                    "captured_minor", "refunded_minor", "currency", "status")}


def funding_info(hold: dict | None) -> dict | None:
    """What audit payloads record about the owner's funding for a purchase."""
    if hold is None:
        return None
    return {"hold_id": hold["id"], "source": hold["source_kind"], "label": hold["source_label"],
            "held_minor": hold["amount_minor"], "captured_minor": hold["captured_minor"],
            "refunded_minor": hold["refunded_minor"], "status": hold["status"], "simulated": True}
