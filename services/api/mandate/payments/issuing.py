"""Sandbox card issuer: the virtual cards behind a mandate.

    mandate confirmed   -> a virtual card for the mandate (the card account the owner controls;
                           a child mandate's card hangs under its parent's card)
    purchase reserved   -> a single-use virtual card under it, locked to one shop, one amount,
                           HKD and the reservation's expiry
    purchase paid       -> the wallet presents the single-use card to the issuer, which runs a
                           card authorization (PAN, expiry, CVV, status, controls) and burns it
    purchase cancelled  -> the single-use card is cancelled
    owner freezes card  -> every card under it declines until it is unfrozen
    mandate revoked     -> the mandate's card is cancelled for good

The mandate card itself is never presented at a shop: only single-use cards issued
from it are, so the agent never holds a reusable card number. The agent only ever
sees a last4.

Card data is handled the way an issuer processor would: the PAN is encrypted at
rest (AES-256-GCM) and looked up by a keyed fingerprint, the CVV is derived from
a card verification key and never stored, and nothing but the last4 leaves this
module. Every authorization attempt, approved or declined, is recorded with an
ISO 8583 response code.

This is a local simulation: the BINs are sandbox ranges, no card network is
contacted and no money moves. A real issuer (Stripe Issuing, Marqeta, a bank's
BIN sponsor) would sit behind the same ``CardIssuer`` methods.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .clock import iso, parse

# Sandbox-only BINs, one per network the rails use. Not routable on a real network.
SANDBOX_BINS = {"mastercard": "222300", "visa": "400000"}
PAN_LENGTH = 16

# Merchant category codes. Grocery shops default to 5411 (grocery stores, supermarkets).
DEFAULT_MCC = "5411"
MERCHANT_MCC: dict[str, str] = {}
# Never allowed on a mandate card: gambling, quasi-cash (crypto, stored value), money transfer.
ALWAYS_BLOCKED_MCCS = ("7995", "6051", "4829")
# A blocked basket category also blocks the shops that only sell it.
CATEGORY_MCCS = {"alcohol": ("5921",)}

# reason -> (ISO 8583 response code, plain message)
DECLINES = {
    "invalid_card_number": ("14", "Invalid card number."),
    "expired_card": ("54", "The card has expired."),
    "cvv_mismatch": ("82", "The security code does not match."),
    "card_cancelled": ("46", "The card is closed."),
    "card_already_used": ("05", "This single-use card was already used."),
    "card_frozen": ("62", "The card is frozen by its owner."),
    "mandate_card_not_presentable": ("57", "This is the mandate card: only single-use cards issued from it can pay a shop."),
    "currency_not_allowed": ("57", "The card only pays in HKD."),
    "merchant_not_allowed": ("57", "The card is locked to other shops."),
    "mcc_blocked": ("57", "The card is blocked for this kind of shop."),
    "over_spend_limit": ("61", "The amount is over the card's limit."),
}


class CardIssuanceError(Exception):
    """The issuer refused to create a card the ledger asked for: a wallet bug, never a business refusal."""


def luhn_check_digit(partial: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(partial)):
        d = int(ch)
        if i % 2 == 0:  # doubled: the check digit will sit to the right of this one
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(pan: str) -> bool:
    return pan.isdigit() and len(pan) >= 12 and luhn_check_digit(pan[:-1]) == pan[-1]


def mcc_for(merchant_id: str) -> str:
    return MERCHANT_MCC.get(merchant_id, DEFAULT_MCC)


def blocked_mccs(policy: dict) -> list[str]:
    mccs = set(ALWAYS_BLOCKED_MCCS)
    for category in policy.get("blocked_categories") or []:
        mccs.update(CATEGORY_MCCS.get(category, ()))
    return sorted(mccs)


@dataclass(frozen=True)
class CardPresentment:
    """What a card network sends the issuer: the card data as typed or tokenised, and the shop."""

    pan: str
    exp_month: int
    exp_year: int
    cvv: str
    merchant_id: str
    mcc: str
    amount_minor: int
    currency: str
    reservation_id: str | None = None


class CardIssuer(Protocol):
    mode: str
    simulated: bool

    def issue_mandate_card(self, conn: sqlite3.Connection, mandate: dict, now) -> dict: ...
    def issue_single_use_card(self, conn: sqlite3.Connection, *, mandate_id: str, reservation_id: str,
                              merchant_id: str, amount_minor: int, currency: str, expires_at: str,
                              network: str, now) -> dict: ...
    def present(self, conn: sqlite3.Connection, card_id: str, *, merchant_id: str, amount_minor: int,
                currency: str, now, reservation_id: str | None = None) -> dict: ...
    def authorize(self, conn: sqlite3.Connection, p: CardPresentment, now) -> dict: ...
    def record_decline(self, conn: sqlite3.Connection, card_id: str, reason: str, *, merchant_id: str,
                       amount_minor: int, currency: str, now, reservation_id: str | None = None) -> dict: ...
    def set_status(self, conn: sqlite3.Connection, card_id: str, status: str, now) -> None: ...
    def cancel_mandate_card(self, conn: sqlite3.Connection, mandate_id: str, now) -> None: ...
    def mandate_card(self, conn: sqlite3.Connection, mandate_id: str) -> dict | None: ...
    def card(self, conn: sqlite3.Connection, card_id: str) -> dict | None: ...


class SandboxCardIssuer:
    mode = "sandbox"
    simulated = True

    def __init__(self, master_key: bytes):
        if len(master_key) < 32:
            raise ValueError("The card vault key must be at least 32 bytes.")
        derive = lambda label: hmac.new(master_key, label, hashlib.sha256).digest()  # noqa: E731
        self._aead = AESGCM(derive(b"mandate/pan-encryption"))
        self._fingerprint_key = derive(b"mandate/pan-fingerprint")
        self._cvk = derive(b"mandate/card-verification")

    @classmethod
    def from_key_dir(cls, key_dir: str | Path) -> "SandboxCardIssuer":
        path = Path(key_dir) / "card_vault.key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
        return cls(path.read_bytes())

    @classmethod
    def ephemeral(cls) -> "SandboxCardIssuer":
        """For tests and throwaway wallets: card numbers become unreadable once the process exits."""
        return cls(secrets.token_bytes(32))

    # card data

    def _fingerprint(self, pan: str) -> str:
        return hmac.new(self._fingerprint_key, pan.encode(), hashlib.sha256).hexdigest()

    def _encrypt(self, pan: str, card_id: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aead.encrypt(nonce, pan.encode(), card_id.encode())

    def _decrypt(self, blob: bytes, card_id: str) -> str:
        return self._aead.decrypt(blob[:12], blob[12:], card_id.encode()).decode()

    def _cvv(self, pan: str, exp_month: int, exp_year: int) -> str:
        """Derived like a CVV2 from a card verification key, so it is never stored."""
        mac = hmac.new(self._cvk, f"{pan}|{exp_month:02d}{exp_year % 100:02d}".encode(), hashlib.sha256)
        return f"{int.from_bytes(mac.digest()[:8], 'big') % 1000:03d}"

    def _new_pan(self, conn, network: str) -> str:
        bin_ = SANDBOX_BINS.get(network, SANDBOX_BINS["mastercard"])
        for _ in range(10):
            body = bin_ + "".join(secrets.choice("0123456789") for _ in range(PAN_LENGTH - len(bin_) - 1))
            pan = body + luhn_check_digit(body)
            if not conn.execute("SELECT 1 FROM virtual_cards WHERE pan_fingerprint = ?",
                                (self._fingerprint(pan),)).fetchone():
                return pan
        raise CardIssuanceError("Could not find an unused card number.")

    # issuance

    def _insert(self, conn, *, owner_id: str, mandate_id: str, parent_card_id: str | None,
                reservation_id: str | None, usage: str, network: str, expires_at: str, spend_limit_minor: int,
                currency: str, merchant_lock: list[str] | None, blocked: list[str], now) -> dict:
        card_id = f"card_{uuid.uuid4().hex}"
        pan = self._new_pan(conn, network)
        valid_thru = parse(expires_at)
        conn.execute(
            "INSERT INTO virtual_cards (id, owner_id, mandate_id, parent_card_id, reservation_id, usage, network, "
            "pan_fingerprint, pan_ciphertext, last4, exp_month, exp_year, expires_at, spend_limit_minor, currency, "
            "merchant_lock_json, blocked_mccs_json, status, issued_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card_id, owner_id, mandate_id, parent_card_id, reservation_id, usage, network, self._fingerprint(pan),
             self._encrypt(pan, card_id), pan[-4:], valid_thru.month, valid_thru.year, expires_at,
             spend_limit_minor, currency, json.dumps(merchant_lock) if merchant_lock is not None else None,
             json.dumps(blocked), "active", iso(now)),
        )
        return self.card(conn, card_id)

    def issue_mandate_card(self, conn, mandate: dict, now) -> dict:
        pol = mandate["policy"]
        parent = self.mandate_card(conn, mandate["parent_mandate_id"]) if mandate.get("parent_mandate_id") else None
        return self._insert(
            conn, owner_id=mandate["owner_id"], mandate_id=mandate["id"],
            parent_card_id=parent["card_id"] if parent else None, reservation_id=None, usage="mandate",
            network="mastercard", expires_at=mandate["expires_at"], spend_limit_minor=pol["per_order_limit_minor"],
            currency=pol["currency"], merchant_lock=pol.get("allowed_merchant_ids"), blocked=blocked_mccs(pol),
            now=now)

    def issue_single_use_card(self, conn, *, mandate_id: str, reservation_id: str, merchant_id: str,
                              amount_minor: int, currency: str, expires_at: str, network: str, now) -> dict:
        parent = self.mandate_card(conn, mandate_id)
        if parent is None:
            raise CardIssuanceError(f"Mandate {mandate_id} has no card.")
        blocked_by = self._inactive_in_chain(conn, parent)
        if blocked_by is not None:
            raise CardIssuanceError(f"Card •••• {blocked_by['last4']} is {blocked_by['status']}.")
        return self._insert(
            conn, owner_id=parent["owner_id"], mandate_id=mandate_id, parent_card_id=parent["card_id"],
            reservation_id=reservation_id, usage="single_use", network=network, expires_at=expires_at,
            spend_limit_minor=amount_minor, currency=currency, merchant_lock=[merchant_id],
            blocked=parent["controls"]["blocked_mccs"], now=now)

    # reads

    @staticmethod
    def _out(row: sqlite3.Row) -> dict:
        return {
            "card_id": row["id"],
            "owner_id": row["owner_id"],
            "mandate_id": row["mandate_id"],
            "parent_card_id": row["parent_card_id"],
            "reservation_id": row["reservation_id"],
            "usage": row["usage"],
            "network": row["network"],
            "last4": row["last4"],
            "exp_month": row["exp_month"],
            "exp_year": row["exp_year"],
            "status": row["status"],
            "controls": {
                "spend_limit_minor": row["spend_limit_minor"],
                "currency": row["currency"],
                "allowed_merchant_ids": json.loads(row["merchant_lock_json"]) if row["merchant_lock_json"] else None,
                "blocked_mccs": json.loads(row["blocked_mccs_json"]),
                "single_use": row["usage"] == "single_use",
                "expires_at": row["expires_at"],
            },
            "issued_at": row["issued_at"],
            "status_changed_at": row["status_changed_at"],
        }

    def card(self, conn, card_id: str) -> dict | None:
        row = conn.execute("SELECT * FROM virtual_cards WHERE id = ?", (card_id,)).fetchone()
        return self._out(row) if row else None

    def mandate_card(self, conn, mandate_id: str) -> dict | None:
        row = conn.execute("SELECT * FROM virtual_cards WHERE mandate_id = ? AND usage = 'mandate'",
                           (mandate_id,)).fetchone()
        return self._out(row) if row else None

    def _inactive_in_chain(self, conn, card: dict) -> dict | None:
        """The nearest card at or above ``card`` that is frozen or cancelled, if any."""
        while card is not None:
            if card["status"] in ("frozen", "cancelled"):
                return card
            card = self.card(conn, card["parent_card_id"]) if card["parent_card_id"] else None
        return None

    def frozen_in_chain(self, conn, mandate_id: str) -> dict | None:
        card = self.mandate_card(conn, mandate_id)
        blocked = self._inactive_in_chain(conn, card) if card else None
        return blocked if blocked is not None and blocked["status"] == "frozen" else None

    def authorizations(self, conn, mandate_ids: list[str], limit: int = 50) -> list[dict]:
        placeholders = ",".join("?" * len(mandate_ids))
        rows = conn.execute(
            f"SELECT a.*, c.last4, c.usage FROM card_authorizations a JOIN virtual_cards c ON c.id = a.card_id "
            f"WHERE a.mandate_id IN ({placeholders}) ORDER BY a.created_at DESC, a.rowid DESC LIMIT ?",
            (*mandate_ids, limit)).fetchall()
        return [{
            "id": r["id"], "card_id": r["card_id"], "card_last4": r["last4"], "card_usage": r["usage"],
            "merchant_id": r["merchant_id"], "mcc": r["mcc"], "amount_minor": r["amount_minor"],
            "currency": r["currency"], "approved": bool(r["approved"]), "response_code": r["response_code"],
            "decline_reason": r["decline_reason"], "message": r["message"], "reservation_id": r["reservation_id"],
            "created_at": r["created_at"],
        } for r in rows]

    # status

    def set_status(self, conn, card_id: str, status: str, now) -> None:
        conn.execute("UPDATE virtual_cards SET status = ?, status_changed_at = ? WHERE id = ?",
                     (status, iso(now), card_id))

    def cancel_card(self, conn, card_id: str, now) -> None:
        conn.execute("UPDATE virtual_cards SET status = 'cancelled', status_changed_at = ? "
                     "WHERE id = ? AND status IN ('active', 'frozen')", (iso(now), card_id))

    def cancel_mandate_card(self, conn, mandate_id: str, now) -> None:
        card = self.mandate_card(conn, mandate_id)
        if card is not None:
            self.cancel_card(conn, card["card_id"], now)

    # authorization

    def _log(self, conn, card_id: str, mandate_id: str, reason: str | None, *, merchant_id: str, mcc: str | None,
             amount_minor: int, currency: str, reservation_id: str | None, now) -> dict:
        code, message = ("00", "Approved.") if reason is None else DECLINES[reason]
        auth_id = f"iauth_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO card_authorizations (id, card_id, mandate_id, merchant_id, mcc, amount_minor, currency, "
            "approved, response_code, decline_reason, message, reservation_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (auth_id, card_id, mandate_id, merchant_id, mcc, amount_minor, currency, int(reason is None), code,
             reason, message, reservation_id, iso(now)))
        return {"id": auth_id, "card_id": card_id, "approved": reason is None, "response_code": code,
                "decline_reason": reason, "message": message}

    def record_decline(self, conn, card_id: str, reason: str, *, merchant_id: str, amount_minor: int,
                       currency: str, now, reservation_id: str | None = None) -> dict:
        card = self.card(conn, card_id)
        return self._log(conn, card_id, card["mandate_id"], reason, merchant_id=merchant_id,
                         mcc=mcc_for(merchant_id), amount_minor=amount_minor, currency=currency,
                         reservation_id=reservation_id, now=now)

    def _decline_reason(self, conn, card: dict, row: sqlite3.Row, p: CardPresentment, now) -> str | None:
        if (p.exp_month, p.exp_year) != (card["exp_month"], card["exp_year"]) or iso(now) >= card["controls"]["expires_at"]:
            return "expired_card"
        pan = self._decrypt(row["pan_ciphertext"], card["card_id"])
        if not hmac.compare_digest(p.cvv, self._cvv(pan, card["exp_month"], card["exp_year"])):
            return "cvv_mismatch"
        if card["status"] == "used":
            return "card_already_used"
        blocked = self._inactive_in_chain(conn, card)
        if blocked is not None:
            return "card_frozen" if blocked["status"] == "frozen" else "card_cancelled"
        if card["usage"] != "single_use":
            return "mandate_card_not_presentable"
        controls = card["controls"]
        if p.currency != controls["currency"]:
            return "currency_not_allowed"
        if controls["allowed_merchant_ids"] is not None and p.merchant_id not in controls["allowed_merchant_ids"]:
            return "merchant_not_allowed"
        if p.mcc in controls["blocked_mccs"]:
            return "mcc_blocked"
        if p.amount_minor > controls["spend_limit_minor"]:
            return "over_spend_limit"
        return None

    def authorize(self, conn, p: CardPresentment, now) -> dict:
        """One card authorization, as the network would send it. Always recorded; a single-use card burns on approval."""
        row = None
        if luhn_valid(p.pan):
            row = conn.execute("SELECT * FROM virtual_cards WHERE pan_fingerprint = ?",
                               (self._fingerprint(p.pan),)).fetchone()
        if row is None:
            # Nothing to attach the attempt to: an unknown number is declined without a card record.
            code, message = DECLINES["invalid_card_number"]
            return {"id": None, "card_id": None, "approved": False, "response_code": code,
                    "decline_reason": "invalid_card_number", "message": message}
        card = self._out(row)
        reason = self._decline_reason(conn, card, row, p, now)
        if reason is None and card["usage"] == "single_use":
            cur = conn.execute("UPDATE virtual_cards SET status = 'used', status_changed_at = ? "
                               "WHERE id = ? AND status = 'active'", (iso(now), card["card_id"]))
            if cur.rowcount != 1:
                reason = "card_already_used"
        return self._log(conn, card["card_id"], card["mandate_id"], reason, merchant_id=p.merchant_id, mcc=p.mcc,
                         amount_minor=p.amount_minor, currency=p.currency, reservation_id=p.reservation_id, now=now)

    def present(self, conn, card_id: str, *, merchant_id: str, amount_minor: int, currency: str, now,
                reservation_id: str | None = None) -> dict:
        """The wallet's checkout presents a card it holds on the agent's behalf, with its full card data."""
        row = conn.execute("SELECT * FROM virtual_cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise CardIssuanceError(f"Unknown card {card_id}.")
        pan = self._decrypt(row["pan_ciphertext"], card_id)
        return self.authorize(conn, CardPresentment(
            pan=pan, exp_month=row["exp_month"], exp_year=row["exp_year"],
            cvv=self._cvv(pan, row["exp_month"], row["exp_year"]), merchant_id=merchant_id,
            mcc=mcc_for(merchant_id), amount_minor=amount_minor, currency=currency, reservation_id=reservation_id,
        ), now)

    def reveal_for_test(self, conn, card_id: str) -> CardPresentment:
        """Full card data for tests that play a shop or a fraudster. No API exposes it."""
        row = conn.execute("SELECT * FROM virtual_cards WHERE id = ?", (card_id,)).fetchone()
        pan = self._decrypt(row["pan_ciphertext"], card_id)
        return CardPresentment(pan=pan, exp_month=row["exp_month"], exp_year=row["exp_year"],
                               cvv=self._cvv(pan, row["exp_month"], row["exp_year"]), merchant_id="",
                               mcc=DEFAULT_MCC, amount_minor=0, currency="HKD")
