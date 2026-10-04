"""Response schemas for the local-only security lab."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

AttackCategory = Literal["control", "spending_limits", "policy", "integrity", "concurrency", "revocation",
                         "access_control", "web_checkout", "scale"]


class AttackSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    category: AttackCategory
    threat: str
    defence: str


class AttackList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attacks: list[AttackSummary]


class AttackStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["setup", "attack"]
    actor: str
    title: str
    method: str
    path: str
    request: dict[str, Any] | None
    http_status: int
    outcome: str
    response: dict[str, Any]


class AttackLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed_payments: int
    paid_total_minor: int
    week_limit_minor: int | None
    week_paid_minor: int | None
    week_reserved_minor: int | None


class AttackSandbox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    isolated: bool
    simulated_time: str
    catalog: str


class AttackResult(AttackSummary):
    expected: str
    observed: str
    held: bool
    error: str | None
    ran_at: str
    sandbox: AttackSandbox
    ledger: AttackLedger | None
    steps: list[AttackStep]
