"""POST /agent-runs and GET /agent-runs/{run_id} from contracts/openapi.json (user role)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role

from .runs import AgentRuns

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


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_agent_run_router(runs: AgentRuns) -> APIRouter:
    router = APIRouter(tags=["agent"])

    @router.post("/agent-runs", status_code=202, response_model=AgentRun)
    def start_run(actor: Annotated[Actor, Depends(_user)], key: IdempotencyKey, body: AgentRunRequest):
        status, run = runs.start(actor, key, body.model_dump(mode="json"))
        return JSONResponse(AgentRun.model_validate(run).model_dump(mode="json"), status_code=status)

    @router.get("/agent-runs/{run_id}", response_model=AgentRun)
    def get_run(actor: Annotated[Actor, Depends(_user)], run_id: str):
        return JSONResponse(AgentRun.model_validate(runs.get(actor, run_id)).model_dump(mode="json"))

    return router
