"""Where the card for a purchase comes from.

A source issues one single-use card for one order, reveals its details only to
the code that types them into the shop's payment fields, and closes it when the
order does not go through. Card numbers never enter run state, events, logs,
API answers or exception text.

Real cards are Timmy's part: his wallet provides a CardSource. Until then only
the test card exists, which real shops decline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol



class CardError(Exception):
    """A card could not be issued or read. The message is safe to show the user."""


@dataclass(frozen=True)
class IssuedCard:
    card_id: str
    last4: str
    funded: str  # what the card holds, for the user ("US$34.00")


class CardSource(Protocol):
    name: str

    def issue(self, amount_minor: int, merchant: str) -> IssuedCard: ...
    def reveal(self, card: IssuedCard) -> dict: ...  # {pan, cvv, exp_month, exp_year, name}
    def close(self, card: IssuedCard) -> None: ...


class TestCardSource:
    """A card-network test number. Real shops decline it, so nothing can be bought with it."""

    name = "test"

    def issue(self, amount_minor: int, merchant: str) -> IssuedCard:
        return IssuedCard("test_card", "4242", f"HK${amount_minor / 100:,.2f} (test card, not funded)")

    def reveal(self, card: IssuedCard) -> dict:
        return {"pan": "4242424242424242", "cvv": "123", "exp_month": 12, "exp_year": 2030, "name": ""}

    def close(self, card: IssuedCard) -> None:
        pass


def card_source_from_env() -> CardSource:
    """The test card until the wallet provides real cards (Timmy's part)."""
    return TestCardSource()
