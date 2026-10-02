"""Prototype bearer-token authentication (contracts/README.md section 2).

Tokens map to an actor ID and role on the server; role and owner are never
read from client headers or bodies. Configure with MANDATE_DEMO_TOKENS, a JSON
object ``{"<token>": {"actor_id": ..., "role": "user"|"agent"|"verifier",
"owner_id": <user id, agents only>}}``. Without it, fixed dev tokens are used:
fine on a laptop, never on a shared network.

Whoever owns the shared app can replace ``current_actor`` via
``app.dependency_overrides`` if one auth module serves every router.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header

from .errors import forbidden, unauthenticated

DEV_TOKENS = {
    "dev-user-token": {"actor_id": "user_demo", "role": "user"},
    "dev-agent-token": {"actor_id": "agent_student", "role": "agent", "owner_id": "user_demo"},
}


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str  # user | agent | verifier
    owner_id: str | None = None  # the user an agent acts for

    @property
    def family_id(self) -> str:
        """The user this actor belongs to; scopes quotes shared by a user and their agents."""
        return self.owner_id if self.role == "agent" and self.owner_id else self.actor_id


@lru_cache(maxsize=1)
def _token_table() -> dict[str, Actor]:
    raw = os.environ.get("MANDATE_DEMO_TOKENS")
    table = json.loads(raw) if raw else DEV_TOKENS
    return {
        token: Actor(actor_id=v["actor_id"], role=v["role"], owner_id=v.get("owner_id"))
        for token, v in table.items()
    }


def current_actor(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthenticated()
    actor = _token_table().get(authorization.removeprefix("Bearer ").strip())
    if actor is None:
        raise unauthenticated()
    return actor


def require_role(actor: Actor, *roles: str) -> Actor:
    if actor.role not in roles:
        raise forbidden(f"Role '{actor.role}' cannot call this operation.")
    return actor
