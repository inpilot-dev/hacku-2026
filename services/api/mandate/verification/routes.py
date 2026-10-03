"""POST /verification/runs as an APIRouter; the shared app (Noah) mounts it.

Auth reuses the wallet's bearer tokens (contracts/README.md section 2); the
contract's x-roles allows only ``user``. Validation errors become 422 through
the wallet's ``install_error_handlers`` on the shared app.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from mandate.payments.auth import Actor, current_actor, require_role

from .model import run


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant: Literal["unsafe", "atomic"]
    max_steps: int = Field(8, ge=1, le=12)
    initial_available_minor: int = Field(ge=1)
    purchase_amounts_minor: list[Annotated[int, Field(ge=1)]] = Field(min_length=2, max_length=2)
    timeout_ms: int = Field(3000, ge=100, le=10000)


class ModelStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int = Field(ge=0)
    actor: str
    action: str
    paid_minor: int = Field(ge=0)
    reserved_minor: int = Field(ge=0)
    remaining_minor: int
    explanation: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    variant: Literal["unsafe", "atomic"]
    status: Literal["counterexample_found", "no_counterexample_within_bound", "inconclusive"]
    solver_result: Literal["sat", "unsat", "unknown", "timeout"]
    max_steps: int = Field(ge=1)
    transaction_count: int = Field(ge=1)
    runtime_ms: int = Field(ge=0)
    assumptions: list[str]
    checked_properties: list[str]
    counterexample: list[ModelStep]
    message: str


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_router() -> APIRouter:
    router = APIRouter(tags=["verification"])

    @router.post("/verification/runs", response_model=VerificationResult)
    def verify_state_model(body: VerificationRequest, _: Actor = Depends(_user)) -> dict:
        # plain def: FastAPI runs it in a worker thread; no DB transaction is held
        return run(**body.model_dump())

    return router
