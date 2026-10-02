set shell := ["bash", "-euo", "pipefail", "-c"]

venv_python := justfile_directory() / ".venv/bin/python"

# Default: run backend and frontend together
default: dev

# List recipes
help:
    @just --list

# Install Python (.venv) and npm dependencies
install:
    uv venv .venv -q
    uv pip install -q --python {{venv_python}} -r services/api/requirements-wallet.txt -r services/mcp-wallet/requirements.txt "mcp>=1.10,<2"
    cd apps/web && npm ci

# Run backend (:8000) and frontend (Vite dev server) together; Ctrl-C stops both
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    (cd services/api && MANDATE_ENABLE_DEMO_CHECKOUT=1 {{venv_python}} -m uvicorn mandate.app:app --reload --port 8000) &
    (cd apps/web && npm run dev) &
    wait

# Run only the backend
backend:
    cd services/api && MANDATE_ENABLE_DEMO_CHECKOUT=1 {{venv_python}} -m uvicorn mandate.app:app --reload --port 8000

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
