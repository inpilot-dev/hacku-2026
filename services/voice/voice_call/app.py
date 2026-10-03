"""Voice approval: phone the caregiver when the wallet opens an approval request.

The wallet POSTs ``approval.opened`` (with a decision token) to ``/approval-opened``. This
service keeps the token, starts an ElevenLabs Agents call through our Twilio number, and
answers the voice agent's tool calls:

    get_order   read the shop, total, items and review reasons back to the caller
    verify_pin  the caregiver's PIN, checked here, never by the model (3 tries)
    approve     only after the PIN passed and the order was read back
    decline     any time

The decision token never leaves this service; ElevenLabs only sees a random call ID. The
wallet still makes the decision and still enforces every hard limit at payment.

If ElevenLabs is not configured, a call is logged and skipped, and the approval stays
pending in the app. Nothing here can change an authorization the wallet already made.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import uuid
from dataclasses import dataclass, field

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import Settings

log = logging.getLogger("voice_call")

OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
MAX_PIN_TRIES = 3


@dataclass
class Call:
    id: str
    approval_id: str
    decision_token: str
    expires_at: str
    pin_ok: bool = False
    pin_tries: int = 0
    read_back: bool = False
    outcome: str | None = None  # approved | declined | locked
    variables: dict = field(default_factory=dict)  # shop and total, for the agent's first message
    lock: threading.Lock = field(default_factory=threading.Lock)


class ToolRequest(BaseModel):
    call_id: str | None = None
    pin: str | None = None
    note: str | None = None


def money(minor: int) -> str:
    return f"HK${minor / 100:.2f}"


def create_app(settings: Settings | None = None, *, wallet_http: httpx.Client | None = None,
               eleven_http: httpx.Client | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    wallet = wallet_http or httpx.Client(base_url=settings.wallet_url, timeout=10)
    eleven = eleven_http or httpx.Client(timeout=15)
    calls: dict[str, Call] = {}
    app = FastAPI(title="Mandate voice approval", version="0.1.0")
    app.state.calls = calls
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET"])

    def check_secret(given: str | None, expected: str | None, what: str) -> None:
        if not expected:
            raise HTTPException(503, f"{what} secret is not configured.")
        if not given or not hmac.compare_digest(given.removeprefix("Bearer ").strip(), expected):
            raise HTTPException(401, f"Wrong {what} secret.")

    # ------------------------------------------------------------ from the wallet

    @app.post("/approval-opened", status_code=202)
    def approval_opened(event: dict, background: BackgroundTasks, authorization: str | None = Header(default=None)):
        check_secret(authorization, settings.webhook_secret, "webhook")
        if event.get("type") != "approval.opened":
            return {"ignored": True}
        approval = event["approval"]
        call = Call(id=f"call_{secrets.token_urlsafe(12)}", approval_id=approval["id"],
                    decision_token=event["decision_token"], expires_at=approval["expires_at"],
                    variables={"shop": approval["merchant_id"], "total": money(approval["amount_minor"])})
        calls[call.id] = call
        background.add_task(place_call, call, approval)
        return {"call_id": call.id}

    def place_call(call: Call, approval: dict) -> None:
        if not settings.can_call:
            log.warning("ElevenLabs is not configured; not calling about %s (call %s). The approval stays in the app.",
                        call.approval_id, call.id)
            return
        body = {
            "agent_id": settings.agent_id, "agent_phone_number_id": settings.phone_number_id,
            "to_number": settings.caregiver_phone,
            "conversation_initiation_client_data": {"dynamic_variables": {"call_id": call.id, **call.variables}},
        }
        try:
            res = eleven.post(OUTBOUND_URL, json=body, headers={"xi-api-key": settings.elevenlabs_api_key})
            res.raise_for_status()
            log.info("Calling the caregiver about %s: %s", call.approval_id, res.json())
        except Exception as exc:  # the app still shows the approval
            log.warning("Could not start the call about %s: %s", call.approval_id, exc)

    # --------------------------------------------------------- from ElevenLabs

    def find(req: ToolRequest, header_call_id: str | None) -> Call:
        call_id = req.call_id or header_call_id
        if call_id:
            call = calls.get(call_id)
        else:
            # The demo runs one call at a time; allow a missing ID only when that is unambiguous.
            open_calls = [c for c in calls.values() if c.outcome is None]
            call = open_calls[0] if len(open_calls) == 1 else None
        if call is None:
            raise HTTPException(404, "No such call.")
        return call

    def tool(name: str):
        def wrap(fn):
            @app.post(f"/tools/{name}", name=name)
            def endpoint(req: ToolRequest, x_tool_secret: str | None = Header(default=None),
                         x_call_id: str | None = Header(default=None)):
                check_secret(x_tool_secret, settings.tool_secret, "tool")
                call = find(req, x_call_id)
                with call.lock:
                    return fn(call, req)
            return fn
        return wrap

    def wallet_get(call: Call) -> dict:
        res = wallet.get("/approval-decision", headers={"Authorization": f"Bearer {call.decision_token}"})
        if res.status_code == 401:
            raise HTTPException(410, "This approval has expired.")
        res.raise_for_status()
        return res.json()

    def wallet_decide(call: Call, verb: str, note: str | None) -> dict:
        res = wallet.post(f"/approval-decision/{verb}", json={"note": note} if note else {},
                          headers={"Authorization": f"Bearer {call.decision_token}",
                                   # Same key for a retried tool call, so it replays instead of failing.
                                   "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{call.id}/{verb}"))})
        if res.status_code == 401:
            return {"ok": False, "say": "This request has expired, so nothing was bought."}
        if res.status_code == 409:
            return {"ok": False, "say": "This request was already answered, so nothing changed."}
        res.raise_for_status()
        return res.json()

    @tool("get_order")
    def get_order(call: Call, req: ToolRequest):
        data = wallet_get(call)
        a, q = data["approval"], data["quote"]
        call.read_back = True
        items = [f"{line['quantity']} x {line['title']}" for line in q["items"]]
        reasons = [r["message"] for r in a["reasons"]]
        return {
            "status": a["status"], "shop": a["merchant_id"], "total": money(a["amount_minor"]),
            "items": items, "reasons": reasons, "expires_at": a["expires_at"],
            "say": f"The order is {money(a['amount_minor'])} at {a['merchant_id']}: {'; '.join(items)}. "
                   f"It needs your OK because: {' '.join(reasons)}",
        }

    @tool("verify_pin")
    def verify_pin(call: Call, req: ToolRequest):
        if call.outcome == "locked":
            return {"ok": False, "say": "Too many wrong PINs. The order was declined."}
        if call.pin_ok:
            return {"ok": True, "say": "PIN already confirmed."}
        given = "".join(ch for ch in (req.pin or "") if ch.isdigit())
        if settings.caregiver_pin and hmac.compare_digest(given, settings.caregiver_pin):
            call.pin_ok = True
            return {"ok": True, "say": "PIN confirmed."}
        call.pin_tries += 1
        left = MAX_PIN_TRIES - call.pin_tries
        if left > 0:
            return {"ok": False, "tries_left": left, "say": f"That PIN is wrong. {left} tries left."}
        call.outcome = "locked"
        wallet_decide(call, "deny", "Declined: too many wrong PINs on the approval call.")
        return {"ok": False, "tries_left": 0, "say": "Too many wrong PINs. I declined the order for safety."}

    @tool("approve")
    def approve(call: Call, req: ToolRequest):
        if call.outcome:
            return {"ok": False, "say": f"This call already ended with: {call.outcome}."}
        if not call.pin_ok:
            return {"ok": False, "say": "I need your PIN before I can approve."}
        if not call.read_back:
            return {"ok": False, "say": "I need to read the order back to you before you approve it."}
        out = wallet_decide(call, "approve", req.note or "Approved on the phone call after PIN and read-back.")
        if "approval" not in out:
            return out
        call.outcome = "approved"
        return {"ok": True, "say": "Approved. The agent can now place this one order."}

    @tool("decline")
    def decline(call: Call, req: ToolRequest):
        if call.outcome:
            return {"ok": False, "say": f"This call already ended with: {call.outcome}."}
        out = wallet_decide(call, "deny", req.note or "Declined on the phone call.")
        if "approval" not in out:
            return out
        call.outcome = "declined"
        return {"ok": True, "say": "Declined. Nothing will be bought."}

    # ------------------------------------------------------- in-app voice backup

    @app.get("/web-session")
    def web_session():
        """Start the same conversation in the browser instead of by phone (no Twilio needed).

        Returns only the agent ID and the call ID: the decision token stays here, and the PIN
        and read-back rules apply exactly as on a phone call.
        """
        open_calls = [c for c in calls.values() if c.outcome is None]
        if not settings.agent_id or len(open_calls) != 1:
            raise HTTPException(404, "No single open approval to talk about.")
        call = open_calls[0]
        return {"agent_id": settings.agent_id, "call_id": call.id, "approval_id": call.approval_id,
                "dynamic_variables": {"call_id": call.id, **call.variables}}

    @app.get("/health")
    def health():
        return {"status": "ok", "can_call": settings.can_call, "open_calls": sum(c.outcome is None for c in calls.values())}

    return app
