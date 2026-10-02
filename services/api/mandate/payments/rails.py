"""Payment rail adapter: the wallet's ledger states mapped onto card-style hold/capture/void.

    reservation reserved           -> hold     (card authorization / Stripe manual-capture intent)
    reservation paid               -> capture
    reservation cancelled/expired  -> void

Only ``SandboxRail`` exists: it moves no real money, and every receipt says
``payment_mode: "sandbox"``. It runs inside the ledger transaction because
it is in-process. A real external rail would need an outbox and
reconciliation instead (docs/mandate-build-plan.md section 2).
"""

from __future__ import annotations

from typing import Protocol


class PaymentRail(Protocol):
    mode: str

    def hold(self, reservation_id: str, amount_minor: int, currency: str) -> str: ...

    def capture(self, reservation_id: str, amount_minor: int, currency: str) -> str: ...

    def void(self, reservation_id: str) -> None: ...


class SandboxRail:
    mode = "sandbox"

    def hold(self, reservation_id: str, amount_minor: int, currency: str) -> str:
        return f"sandbox_hold_{reservation_id}"

    def capture(self, reservation_id: str, amount_minor: int, currency: str) -> str:
        return f"sandbox_capture_{reservation_id}"

    def void(self, reservation_id: str) -> None:
        return None
