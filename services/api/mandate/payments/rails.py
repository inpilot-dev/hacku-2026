"""Payment rail adapters: the wallet's ledger states mapped onto rail steps.

    mandate confirmed              -> open a rail account (card profile or eDDA authorisation)
                                      with limit = per-order cap and expiry = mandate expiry
    reservation reserved           -> mint a single-use credential for this one purchase
    reservation paid               -> present the credential once (capture)
    reservation cancelled/expired  -> void the credential
    payment refunded               -> refund on the rail
    mandate revoked                -> close the rail account

A single-use credential is locked to one merchant, one maximum amount, one
currency, one expiry and the wallet's token id, and it works for exactly one
successful presentment: a second capture, a different merchant or a larger
amount is declined by the rail itself, as a second line of defence behind the
ledger.

On the card rails the credential is a real-format virtual card from the card
issuer (issuing.py): a Luhn-valid number under the mandate's card, and capture
is a card authorization the issuer runs against that card's controls. The FPS
rail has no card.

On the card rails the owner's own money pays for each single-use card
(funding.py): the exact authorized amount is held on the owner's funding
source when the card is issued, captured once the issuer approves the card,
released when the card is cancelled and refunded with the payment.

No real rail is connected. All three rails are local simulations shaped like
the products they name (HKT's Tap & Go Single Use Card, a scoped card network
token, an FPS eDDA debit): no money moves, receipts say
``payment_mode: "sandbox"`` and audit payloads carry ``rail.simulated: true``.

Adapters run inside the ledger's transaction on the same connection, which
is only valid for in-process simulations. A real external rail would need an
outbox and reconciliation instead (docs/mandate-build-plan.md section 2).
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Protocol

from .clock import iso
from .funding import FundingDeclined, SimulatedFundingSource, funding_info
from .issuing import CardIssuanceError, CardIssuer


class RailDeclined(Exception):
    """The rail refused an operation the ledger had already approved: a wallet bug, never a business refusal."""


class PaymentRail(Protocol):
    name: str
    network: str | None
    mode: str  # what receipts report; the contract allows only "sandbox" in v0
    simulated: bool
    holds_funds_at_rail: bool

    def issue(self, conn: sqlite3.Connection, mandate: dict, now) -> str: ...
    def hold(self, conn: sqlite3.Connection, reservation: dict, now) -> dict: ...
    def capture(self, conn: sqlite3.Connection, reservation: dict, now) -> str: ...
    def void(self, conn: sqlite3.Connection, reservation: dict, now) -> None: ...
    def refund(self, conn: sqlite3.Connection, reservation: dict, amount_minor: int, now) -> str: ...
    def close(self, conn: sqlite3.Connection, mandate_id: str, now) -> None: ...
    def funding_for(self, conn: sqlite3.Connection, reservation_id: str) -> dict | None: ...


class SimulatedSingleUseRail:
    """One rail account per mandate; one single-use credential per reservation."""

    mode = "sandbox"
    simulated = True

    def __init__(self, name: str, *, network: str | None, prefix: str, holds_funds_at_rail: bool,
                 max_amount_minor: int | None = None, issuer: CardIssuer | None = None,
                 card_network: str | None = None, funding: SimulatedFundingSource | None = None):
        self.name = name
        self.network = network
        self.prefix = prefix
        self.holds_funds_at_rail = holds_funds_at_rail
        self.max_amount_minor = max_amount_minor
        # With an issuer, each credential is a single-use virtual card on ``card_network``.
        self.issuer = issuer
        self.card_network = card_network
        # With a funding source, the owner's money is held for each credential (only with an issuer).
        self.funding = funding if issuer is not None else None

    # accounts

    def issue(self, conn, mandate: dict, now) -> str:
        limit = mandate["policy"]["per_order_limit_minor"]
        if self.max_amount_minor is not None:
            limit = min(limit, self.max_amount_minor)
        ref = f"{self.prefix}acct_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO rail_accounts (rail, mandate_id, ref, limit_minor, currency, expires_at, status, issued_at) "
            "VALUES (?,?,?,?,?,?,'active',?)",
            (self.name, mandate["id"], ref, limit, "HKD", mandate["expires_at"], iso(now)),
        )
        return ref

    def account(self, conn, mandate_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM rail_accounts WHERE rail = ? AND mandate_id = ?",
                           (self.name, mandate_id)).fetchone()
        if row is None:
            raise RailDeclined(f"No {self.name} account for mandate {mandate_id}.")
        return row

    def close(self, conn, mandate_id: str, now) -> None:
        conn.execute("UPDATE rail_accounts SET status = 'closed', closed_at = ? "
                     "WHERE rail = ? AND mandate_id = ? AND status = 'active'", (iso(now), self.name, mandate_id))

    # credentials

    def hold(self, conn, reservation: dict, now) -> dict:
        acct = self.account(conn, reservation["mandate_id"])
        if acct["status"] != "active":
            raise RailDeclined("Rail account is closed.")
        if iso(now) >= acct["expires_at"]:
            raise RailDeclined("Rail account has expired.")
        if reservation["amount_minor"] > acct["limit_minor"]:
            raise RailDeclined("Amount is over the rail account limit.")
        if self.issuer is not None:
            try:
                card = self.issuer.issue_single_use_card(
                    conn, mandate_id=reservation["mandate_id"], reservation_id=reservation["id"],
                    merchant_id=reservation["merchant_id"], amount_minor=reservation["amount_minor"], currency="HKD",
                    expires_at=reservation["expires_at"], network=self.card_network, now=now)
            except CardIssuanceError as exc:
                raise RailDeclined(str(exc)) from None
            credential_id, last4 = card["card_id"], card["last4"]
            if self.funding is not None:
                self.funding.hold(conn, reservation, credential_id, now)
        else:
            digits = str(uuid.uuid4().int)
            credential_id = f"{self.prefix}_{uuid.uuid4().hex}"
            last4 = digits[-4:] if self.network not in (None, "fps") else None
        credential = {
            "credential_id": credential_id,
            "rail": self.name,
            "network": self.network,
            "last4": last4,
            "merchant_id": reservation["merchant_id"],
            "amount_minor": reservation["amount_minor"],
            "currency": "HKD",
            "expires_at": reservation["expires_at"],
            "single_use": True,
            "holds_funds_at_rail": self.holds_funds_at_rail,
            "purpose": reservation["purpose"],
        }
        conn.execute(
            "INSERT INTO rail_payments (reservation_id, rail, account_ref, credential_id, last4, merchant_id, "
            "amount_minor, currency, purpose, token_id, expires_at, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'held',?,?)",
            (reservation["id"], self.name, acct["ref"], credential["credential_id"], credential["last4"],
             credential["merchant_id"], credential["amount_minor"], "HKD", credential["purpose"],
             reservation["token_id"], credential["expires_at"], iso(now), iso(now)),
        )
        return credential

    def present(self, conn, credential_id: str, merchant_id: str, amount_minor: int, now) -> str:
        """A merchant presents the credential. It succeeds once, for its own merchant, within amount and expiry."""
        row = conn.execute("SELECT * FROM rail_payments WHERE credential_id = ?", (credential_id,)).fetchone()
        if row is None:
            raise RailDeclined("Unknown credential.")
        if row["status"] != "held":
            raise RailDeclined(f"Credential is {row['status']}; a single-use credential works once.")
        if row["merchant_id"] != merchant_id:
            raise RailDeclined(f"Credential is locked to {row['merchant_id']}.")
        if amount_minor > row["amount_minor"]:
            raise RailDeclined("Amount is over the credential's limit.")
        if iso(now) >= row["expires_at"]:
            raise RailDeclined("Credential has expired.")
        acct = conn.execute("SELECT status FROM rail_accounts WHERE ref = ?", (row["account_ref"],)).fetchone()
        if acct is None or acct["status"] != "active":
            raise RailDeclined("Rail account is closed.")
        if self.issuer is not None:
            auth = self.issuer.present(conn, credential_id, merchant_id=merchant_id, amount_minor=amount_minor,
                                       currency=row["currency"], now=now, reservation_id=row["reservation_id"])
            if not auth["approved"]:
                raise RailDeclined(f"Card issuer declined ({auth['response_code']}): {auth['message']}")
            if self.funding is not None:
                try:
                    self.funding.capture(conn, row["reservation_id"], amount_minor, now)
                except FundingDeclined as exc:
                    raise RailDeclined(str(exc)) from None
        cur = conn.execute("UPDATE rail_payments SET status = 'captured', captured_minor = ?, updated_at = ? "
                           "WHERE credential_id = ? AND status = 'held'", (amount_minor, iso(now), credential_id))
        if cur.rowcount != 1:
            raise RailDeclined("Credential changed during capture.")
        return credential_id

    def funding_for(self, conn, reservation_id: str) -> dict | None:
        return self.funding.get(conn, reservation_id) if self.funding is not None else None

    def _row(self, conn, reservation: dict) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM rail_payments WHERE reservation_id = ?", (reservation["id"],)).fetchone()
        if row is None:
            raise RailDeclined(f"No credential for reservation {reservation['id']}.")
        return row

    def capture(self, conn, reservation: dict, now) -> str:
        row = self._row(conn, reservation)
        return self.present(conn, row["credential_id"], reservation["merchant_id"], reservation["amount_minor"], now)

    def void(self, conn, reservation: dict, now) -> None:
        cur = conn.execute("UPDATE rail_payments SET status = 'voided', updated_at = ? "
                           "WHERE reservation_id = ? AND status = 'held'", (iso(now), reservation["id"]))
        if cur.rowcount != 1:
            raise RailDeclined(f"No open credential for reservation {reservation['id']}.")
        if self.issuer is not None:
            self.issuer.cancel_card(conn, self._row(conn, reservation)["credential_id"], now)
        if self.funding is not None:
            try:
                self.funding.release(conn, reservation["id"], now)
            except FundingDeclined as exc:
                raise RailDeclined(str(exc)) from None

    def refund(self, conn, reservation: dict, amount_minor: int, now) -> str:
        row = self._row(conn, reservation)
        if row["status"] != "captured" or amount_minor > row["captured_minor"]:
            raise RailDeclined("Only a captured payment can be refunded, up to the captured amount.")
        conn.execute("UPDATE rail_payments SET status = 'refunded', refunded_minor = ?, updated_at = ? "
                     "WHERE reservation_id = ?", (amount_minor, iso(now), reservation["id"]))
        if self.funding is not None:
            try:
                self.funding.refund(conn, reservation["id"], amount_minor, now)
            except FundingDeclined as exc:
                raise RailDeclined(str(exc)) from None
        return f"{self.prefix}rf_{uuid.uuid4().hex}"


def TapAndGoSingleUseCardSimulator(issuer: CardIssuer | None = None) -> SimulatedSingleUseRail:
    """HKT Tap & Go Single Use Card: virtual prepaid Mastercard, one payment per card, HK$2,000 maximum."""
    return SimulatedSingleUseRail("tap_and_go_single_use_card", network="mastercard", prefix="tngsuc",
                                  holds_funds_at_rail=True, max_amount_minor=200000, issuer=issuer,
                                  card_network="mastercard",
                                  funding=SimulatedFundingSource("stored_value", "Tap & Go wallet balance"))


def CardNetworkTokenSimulator(issuer: CardIssuer | None = None) -> SimulatedSingleUseRail:
    """A card tokenised per purchase (the Mastercard agent-token / Visa network-token pattern)."""
    return SimulatedSingleUseRail("card_network_token", network="card", prefix="ntok", holds_funds_at_rail=True,
                                  issuer=issuer, card_network="visa",
                                  funding=SimulatedFundingSource("card_authorization", "HSBC Red credit card"))


def FpsEddaSimulator() -> SimulatedSingleUseRail:
    """FPS debit under an eDDA authorisation: limit and expiry, but no hold, so the reservation lives in the ledger."""
    return SimulatedSingleUseRail("fps_edda", network="fps", prefix="fps", holds_funds_at_rail=False)


def default_rails(issuer: CardIssuer | None = None) -> dict[str, SimulatedSingleUseRail]:
    rails = (TapAndGoSingleUseCardSimulator(issuer), CardNetworkTokenSimulator(issuer), FpsEddaSimulator())
    return {r.name: r for r in rails}


def rail_info(rail: PaymentRail, ref: str | None = None, funding: dict | None = None) -> dict:
    """What audit payloads record about the rail and the owner's funding; never a credential secret."""
    info = {"name": rail.name, "simulated": rail.simulated, "ref": ref}
    if funding is not None:
        info["funding"] = funding_info(funding)
    return info
