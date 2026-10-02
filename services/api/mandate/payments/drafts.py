"""How the wallet looks up a draft at confirmation.

Drafts belong to Abdullah's module. The wallet only needs a draft's owner,
delegatee, parent and expiry, so it takes any callable
``draft_id -> dict | None`` returning those DraftResponse fields. Until the
agent module is wired in, ``InMemoryDrafts`` serves tests and the dev app.
"""

from __future__ import annotations

from typing import Callable, Optional

DraftLookup = Callable[[str], Optional[dict]]


class InMemoryDrafts:
    def __init__(self):
        self._drafts: dict[str, dict] = {}

    def register(self, draft_id: str, *, owner_id: str, delegatee_id: str, expires_at: str,
                 parent_mandate_id: str | None = None) -> None:
        self._drafts[draft_id] = {
            "draft_id": draft_id,
            "owner_id": owner_id,
            "delegatee_id": delegatee_id,
            "parent_mandate_id": parent_mandate_id,
            "expires_at": expires_at,
        }

    def __call__(self, draft_id: str) -> dict | None:
        return self._drafts.get(draft_id)
