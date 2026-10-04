"""Shared application composition for the web prototype and wallet API."""
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mandate.commerce.routes import build_commerce_router
from mandate.payments.clock import iso
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts
from mandate.payments.web_cards import WalletCardSource
from mandate.agent.catalog_routes import build_catalog_router
from mandate.agent.drafts import DraftService
from mandate.agent.purchase.profile import ProfileStore
from mandate.agent.purchase.routes import build_purchase_router
from mandate.agent.purchase.runs import PurchaseRuns
from mandate.agent.run_routes import build_agent_run_router
from mandate.agent.runs import AgentRuns
from mandate.integration.attack_lab import build_attack_lab_router
from mandate.integration.demo_routes import build_demo_router
from mandate.stores.carts import CartSync
from mandate.stores.connections import StoreConnections
from mandate.stores.routes import build_store_router
from mandate.stores.steel import SteelStoreBrowser
from mandate.audit.routes import build_router as build_audit_router
from mandate.verification import build_router as build_verification_router


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = REPOSITORY_ROOT / "apps" / "web" / "dist"


def build_app():
    drafts = InMemoryDrafts()
    drafts.register("draft_demo", owner_id="user_demo", delegatee_id="agent_student",
                    expires_at="2026-10-31T23:59:59+08:00")
    app, wallet = create_app(draft_lookup=drafts)
    app.title = "Mandate family wallet"
    app.version = "0.1.0"
    app.include_router(build_commerce_router(wallet), prefix="/api/v1")
    app.include_router(build_demo_router(wallet), prefix="/api/v1")
    app.include_router(build_attack_lab_router(), prefix="/api/v1")
    app.include_router(build_catalog_router(wallet), prefix="/api/v1")
    app.include_router(build_agent_run_router(AgentRuns(wallet), DraftService(wallet, drafts), warm_transcription=True),
                       prefix="/api/v1")
    profiles = ProfileStore()
    app.include_router(build_purchase_router(PurchaseRuns(profiles, cards=WalletCardSource(wallet)), profiles),
                       prefix="/api/v1")
    store_browser = SteelStoreBrowser()
    stores = StoreConnections(store_browser, wallet.clock.now)
    app.include_router(build_store_router(stores, CartSync(wallet, stores, store_browser)), prefix="/api/v1")
    # Audit forwards checkpoints/checks to the separate verifier process (MANDATE_VERIFIER_URL).
    app.include_router(build_audit_router(wallet.db), prefix="/api/v1")
    app.include_router(build_verification_router(), prefix="/api/v1")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "api_version": "0.1.0", "server_time": iso(wallet.clock.now()),
                "payment_mode": "sandbox"}

    # Production-style local demo: build apps/web first, then API and SPA share
    # one origin. Keep API routes registered above this fallback so API errors
    # remain JSON errors instead of receiving index.html.
    assets = WEB_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.api_route("/api/{api_path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    def unknown_api_route(api_path: str):
        raise HTTPException(status_code=404, detail="API route not found.")

    @app.get("/")
    def web_index():
        index = WEB_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Build apps/web before serving the single-origin demo.")
        return FileResponse(index)

    @app.get("/{path:path}")
    def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found.")
        root = WEB_DIST.resolve()
        candidate = (root / path).resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Build apps/web before serving the single-origin demo.")
        return FileResponse(index)

    return app


app = build_app()
