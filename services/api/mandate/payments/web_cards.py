"""The wallet as the card source for one-time web purchases (agent/purchase/cards.py).

    issue   -> the wallet authorizes the approved checkout total against the owner's allowance (it must allow web
               purchases; per-order limit, budgets, velocity, freeze and revocation all apply) and holds a
               single-use card locked to that shop and total
    reveal  -> the card's number, expiry and CVV, read from the issuer only while the authorization is open
    settle  -> the shop confirmed the order: the card is captured, the budget moves from reserved to paid and a
               receipt is written
    close   -> nothing was ordered: the card is cancelled and the reserved amount released

The purchase run only ever holds an ``IssuedCard`` (an id, a last4 and a line for the user). The authorization
token stays here, in memory, for the settle step.
"""

from __future__ import annotations

import logging
import threading

from mandate.agent.purchase.cards import CardError, IssuedCard

from .auth import Actor
from .errors import ApiError
from .policy import money
from .service import Wallet

log = logging.getLogger(__name__)


class WalletCardSource:
    name = "wallet"

    def __init__(self, wallet: Wallet):
        self.wallet = wallet
        self._held: dict[str, tuple[Actor, str]] = {}  # transaction id -> (owner, authorization token)
        self._lock = threading.Lock()

    def _entry(self, card: IssuedCard) -> tuple[Actor, str]:
        with self._lock:
            entry = self._held.get(card.card_id)
        if entry is None:
            raise CardError("The wallet no longer holds this card.")
        return entry

    def issue(self, amount_minor: int, merchant: str, *, actor: Actor, purchase_id: str, title: str,
              url: str) -> IssuedCard:
        try:
            body = self.wallet.authorize_web_purchase(actor, purchase_id=purchase_id, merchant_id=merchant,
                                                      amount_minor=amount_minor, title=title, url=url)
        except ApiError as exc:
            raise CardError(exc.message) from None
        if body["status"] != "approved":
            raise CardError(f"The wallet refused this purchase: {body['message']}")
        credential = body["payment_credential"]
        with self._lock:
            self._held[body["transaction_id"]] = (actor, body["authorization_token"])
        return IssuedCard(body["transaction_id"], credential["last4"],
                          f"{money(amount_minor)} held on allowance {body['mandate_id']} until "
                          f"{credential['expires_at']}, sandbox card")

    def reveal(self, card: IssuedCard) -> dict:
        actor, _token = self._entry(card)
        try:
            details = self.wallet.web_card_details(actor, card.card_id)
        except ApiError as exc:
            raise CardError(exc.message) from None
        return {**details, "name": ""}

    def settle(self, card: IssuedCard) -> None:
        actor, token = self._entry(card)
        try:
            body = self.wallet.settle_web_purchase(actor, card.card_id, token)
        except ApiError as exc:
            raise CardError(exc.message) from None
        if body["status"] != "completed":
            raise CardError(f"The wallet could not record the payment: {body['message']}")
        with self._lock:
            self._held.pop(card.card_id, None)

    def close(self, card: IssuedCard) -> None:
        with self._lock:
            entry = self._held.pop(card.card_id, None)
        if entry is None:
            return
        try:
            self.wallet.release_web_purchase(entry[0], card.card_id, "Nothing was ordered at the shop.")
        except ApiError:
            # The reservation still expires on its own and releases the amount then.
            log.exception("Releasing web purchase %s failed", card.card_id)
