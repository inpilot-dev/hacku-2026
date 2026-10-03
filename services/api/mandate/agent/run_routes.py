"""POST /agent-runs and GET /agent-runs/{run_id} from contracts/openapi.json (user role)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.errors import ApiError

from .drafts import DraftService
from .runs import AgentRuns
from .shopping_list import ShoppingListError, ShoppingListService, warm_whisper

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShoppingItem(_Strict):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    quantity: Annotated[StrictInt, Field(ge=1, le=99)]
    unit: StrictStr | None = None


class AgentRunRequest(_Strict):
    mandate_id: StrictStr
    shopping_list: Annotated[list[ShoppingItem], Field(min_length=1, max_length=30)]
    instruction: Annotated[StrictStr, Field(max_length=500)] | None = None
    auto_purchase: StrictBool = False


class AgentRun(_Strict):
    id: StrictStr
    mandate_id: StrictStr
    status: Literal["queued", "running", "awaiting_review", "quoted", "completed", "refused", "failed"]
    provider: Literal["jev", "fallback", "scripted"]
    model_id: StrictStr | None
    execution_mode: Literal["local", "cloud", "scripted"]
    quote_id: StrictStr | None
    transaction_id: StrictStr | None
    payment_id: StrictStr | None
    latest_decision_id: StrictStr | None
    message: StrictStr
    created_at: StrictStr
    updated_at: StrictStr


class DraftRequest(_Strict):
    text: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    delegatee_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    parent_mandate_id: StrictStr | None = None


class ParseRequest(_Strict):
    text: Annotated[StrictStr, Field(min_length=1, max_length=2000)]


class TranscribeRequest(_Strict):
    audio_base64: Annotated[StrictStr, Field(min_length=1, max_length=6_000_000)]
    format: Annotated[StrictStr, Field(min_length=2, max_length=8)]


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_agent_run_router(runs: AgentRuns, drafts: DraftService | None = None,
                           shopping_list: ShoppingListService | None = None,
                           warm_transcription: bool = False) -> APIRouter:
    router = APIRouter(tags=["agent"])
    shopping_list = shopping_list or ShoppingListService()
    if warm_transcription:
        warm_whisper()

    def _fail(exc: ShoppingListError):
        raise ApiError(exc.status, "SERVICE_UNAVAILABLE" if exc.status == 503 else "INVALID_REQUEST", str(exc),
                       retryable=exc.status == 503) from exc

    @router.post("/shopping-list/parse")
    def parse_list(_actor: Annotated[Actor, Depends(_user)], body: ParseRequest):
        parsed = shopping_list.parse(body.text)
        return JSONResponse({"items": parsed.items, "source": parsed.source, "model_id": parsed.model_id,
                             "note": parsed.note})

    @router.post("/shopping-list/transcribe")
    def transcribe(_actor: Annotated[Actor, Depends(_user)], body: TranscribeRequest):
        try:
            text, model_id = shopping_list.transcribe(body.audio_base64, body.format)
        except ShoppingListError as exc:
            _fail(exc)
        return JSONResponse({"text": text, "model_id": model_id})

    if drafts is not None:
        @router.post("/mandates/draft", status_code=201)
        def create_draft(actor: Annotated[Actor, Depends(_user)], key: IdempotencyKey, body: DraftRequest):
            status, draft = drafts.create(actor, key, body.model_dump(mode="json"))
            return JSONResponse(draft, status_code=status)

    @router.post("/agent-runs", status_code=202, response_model=AgentRun)
    def start_run(actor: Annotated[Actor, Depends(_user)], key: IdempotencyKey, body: AgentRunRequest):
        status, run = runs.start(actor, key, body.model_dump(mode="json"))
        return JSONResponse(AgentRun.model_validate(run).model_dump(mode="json"), status_code=status)

    @router.get("/agent-runs/{run_id}", response_model=AgentRun)
    def get_run(actor: Annotated[Actor, Depends(_user)], run_id: str):
        return JSONResponse(AgentRun.model_validate(runs.get(actor, run_id)).model_dump(mode="json"))

    return router
