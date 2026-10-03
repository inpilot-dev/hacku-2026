"""Standalone wallet app for development until the shared app exists.

    cd services/api && uvicorn --factory mandate.payments.dev_app:seeded_app --port 8000

Uses dev tokens (``dev-user-token``, ``dev-agent-token``), the placeholder
catalog and a seeded draft ``draft_demo`` (owner user_demo, delegatee
agent_student). Data lives in MANDATE_WALLET_DATA_DIR (default
services/api/.data/wallet).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from mandate.storage.db import Database

from .catalog import Catalog
from .drafts import InMemoryDrafts
from .issuing import SandboxCardIssuer
from .errors import install_error_handlers
from .routes import build_router
from .service import Wallet
from .signing import Signer


def create_app(data_dir: str | Path | None = None, **wallet_kwargs) -> tuple[FastAPI, Wallet]:
    data_dir = Path(data_dir or os.environ.get("MANDATE_WALLET_DATA_DIR")
                    or Path(__file__).resolve().parents[2] / ".data" / "wallet")
    db = Database(data_dir / "wallet.sqlite3")
    db.migrate()
    key_dir = os.environ.get("MANDATE_WALLET_KEY_DIR") or data_dir / "keys"
    signer = Signer.from_key_dir(key_dir)
    wallet_kwargs.setdefault("draft_lookup", InMemoryDrafts())
    wallet_kwargs.setdefault("issuer", SandboxCardIssuer.from_key_dir(key_dir))
    wallet = Wallet(db, signer, Catalog.load(), **wallet_kwargs)

    app = FastAPI(title="Mandate wallet (dev)", version="0.1.0")
    install_error_handlers(app)
    app.include_router(build_router(wallet), prefix="/api/v1")
    return app, wallet


def seeded_app() -> FastAPI:
    drafts = InMemoryDrafts()
    drafts.register("draft_demo", owner_id="user_demo", delegatee_id="agent_student",
                    expires_at="2026-10-31T23:59:59+08:00")
    app, _ = create_app(draft_lookup=drafts)
    return app

