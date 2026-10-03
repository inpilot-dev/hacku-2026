set shell := ["bash", "-euo", "pipefail", "-c"]

venv_python := justfile_directory() / ".venv/bin/python"

# Default: run backend and frontend together
default: dev

# List recipes
help:
    @just --list

# Install Python (.venv) and npm dependencies
install:
    uv venv .venv -q --allow-existing
    uv pip install -q --python {{venv_python}} -r services/api/requirements-wallet.txt -r services/api/requirements-verification.txt -r services/mcp-wallet/requirements.txt -r services/agent/requirements.txt -r services/voice/requirements.txt "mcp>=1.10,<2"
    cd apps/web && npm ci

# Run backend (:8000) and frontend (Vite dev server) together; Ctrl-C stops both
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    (cd services/api && MANDATE_CATALOG_PATH=../../data/catalog/wellcome.json MANDATE_ENABLE_DEMO_CHECKOUT=1 {{venv_python}} -m uvicorn mandate.app:app --reload --port 8000) &
    (cd apps/web && npm run dev) &
    ./scripts/start-verifier.sh {{venv_python}} .data/audit/keys .data/verifier &
    wait

# Run only the backend
backend:
    cd services/api && MANDATE_ENABLE_DEMO_CHECKOUT=1 {{venv_python}} -m uvicorn mandate.app:app --reload --port 8000

# Run only the independent audit verifier (:8201); `dev` and `demo` start it for you
verifier:
    ./scripts/start-verifier.sh {{venv_python}} .data/audit/keys .data/verifier

# Run only the frontend
frontend:
    cd apps/web && npm run dev

# Build the frontend and serve it with the API on one origin (fresh temp wallet)
demo:
    cd apps/web && npm run build
    PYTHON_BIN={{venv_python}} ./scripts/run-demo.sh

# Run all tests
test:
    cd services/api && {{venv_python}} -m pytest -q
    cd services/mcp-wallet && {{venv_python}} -m pytest -q
    cd services/agent && {{venv_python}} -m pytest -q
    cd services/voice && {{venv_python}} -m pytest -q

# Voice approval call service (:8300); optional, see services/voice/README.md
voice:
    cd services/voice && {{venv_python}} -m uvicorn voice_call.app:create_app --factory --port 8300

# Capture a fresh observed Wellcome catalog into data/catalog (makes ~8 HTTP requests)
capture:
    cd services/agent && {{venv_python}} -m catalog_capture

# Start a self-hosted Steel browser for Jev (viewer: http://localhost:3000/ui)
steel:
    docker rm -f steel >/dev/null 2>&1 || true
    docker run -d --name steel -p 127.0.0.1:3000:3000 -p 127.0.0.1:9223:9223 -e DOMAIN=localhost:3000 -e CDP_DOMAIN=localhost:9223 ghcr.io/steel-dev/steel-browser:latest

# Browser capture with Jev: just browse wellcome rice=pantry broccoli=produce
browse store +items:
    cd services/agent && {{venv_python}} -m catalog_capture.browse --store {{store}} {{prepend("--item ", items)}}

# Run only the backend, pricing quotes from the observed Wellcome catalog
backend-observed:
    cd services/api && MANDATE_CATALOG_PATH=../../data/catalog/wellcome.json MANDATE_ENABLE_DEMO_CHECKOUT=1 {{venv_python}} -m uvicorn mandate.app:app --reload --port 8000
