"""Shared application composition for the web prototype and wallet API."""
from mandate.payments.clock import iso
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts
from mandate.payments.service import Wallet
from mandate.integration.demo_routes import build_demo_router


def build_app():
    drafts = InMemoryDrafts()
    drafts.register("draft_demo", owner_id="user_demo", delegatee_id="agent_student",
                    expires_at="2026-10-31T23:59:59+08:00")
    app, wallet = create_app(draft_lookup=drafts)
    app.title = "Mandate family wallet"
    app.version = "0.1.0"
    app.include_router(build_demo_router(wallet), prefix="/api/v1")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "api_version": "0.1.0", "server_time": iso(wallet.clock.now()),
                "payment_mode": "sandbox"}

    return app


app = build_app()
