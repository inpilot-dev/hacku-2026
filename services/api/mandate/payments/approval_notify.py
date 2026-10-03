"""Tell something outside the wallet that a purchase is waiting for the owner's approval.

Off by default: without MANDATE_APPROVAL_WEBHOOK_URL the wallet notifies nobody and the
owner answers in the app, exactly as before. With it set, every newly opened approval
request is POSTed to that URL (for example the voice-call service) together with a
decision token.

A decision token is an Ed25519 JWS with its own audience. It can approve or deny the one
approval it names, until that approval expires, and nothing else: it is never accepted as
a purchase authorization, and it cannot read or change any other approval or mandate.
The approval itself can only be decided once, so the token is single-use in effect.

Delivery runs on a background thread after the wallet has committed. A slow, failing or
missing receiver never changes the authorization result; the approval simply stays
pending in the app until it is answered or expires.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Protocol

import httpx

from .clock import iso
from .signing import APPROVAL_AUDIENCE, Signer

log = logging.getLogger(__name__)

PURPOSE = "approval_decision"


class ApprovalNotifier(Protocol):
    def approval_opened(self, event: dict) -> None: ...


class NoNotifier:
    def approval_opened(self, event: dict) -> None:
        pass


class WebhookNotifier:
    def __init__(self, url: str, secret: str | None = None, timeout_s: float = 5.0):
        self.url, self.secret, self.timeout_s = url, secret, timeout_s

    def approval_opened(self, event: dict) -> None:
        threading.Thread(target=self._post, args=(event,), daemon=True, name="approval-webhook").start()

    def _post(self, event: dict) -> None:
        headers = {"Authorization": f"Bearer {self.secret}"} if self.secret else {}
        try:
            httpx.post(self.url, json=event, headers=headers, timeout=self.timeout_s).raise_for_status()
        except Exception as exc:  # the receiver is optional; the app still shows the approval
            log.warning("Approval webhook to %s failed for %s: %s", self.url, event["approval"]["id"], exc)


def notifier_from_env() -> ApprovalNotifier:
    url = os.environ.get("MANDATE_APPROVAL_WEBHOOK_URL")
    if not url:
        return NoNotifier()
    return WebhookNotifier(url, os.environ.get("MANDATE_APPROVAL_WEBHOOK_SECRET"))


def decision_token(signer: Signer, approval: dict, now) -> str:
    return signer.sign({
        "token_id": f"adt_{approval['id']}", "purpose": PURPOSE, "approval_id": approval["id"],
        "owner_id": approval["owner_id"], "issued_at": iso(now), "expires_at": approval["expires_at"],
    }, audience=APPROVAL_AUDIENCE)


def opened_event(signer: Signer, approval: dict, owner_id: str, now) -> dict:
    return {
        "type": "approval.opened",
        "approval": approval,
        "owner_id": owner_id,
        "decision_token": decision_token(signer, {**approval, "owner_id": owner_id}, now),
    }
