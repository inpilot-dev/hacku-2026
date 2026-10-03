# Mandate — HacKU 2026

Mandate is a shopping-agent prototype with a wallet that enforces user-approved spending limits. The browser builds or reviews a basket; the wallet calculates the total and decides whether it may proceed. All payments in this repository use a local simulator. **No real funds move.**

## Run the prototype

Prerequisites: Python 3.12+, Node.js/npm, [`uv`](https://docs.astral.sh/uv/) and [`just`](https://just.systems/).

```bash
just install
just dev
```

Open <http://127.0.0.1:5173/>. This starts the React client and shared FastAPI wallet using the captured Wellcome catalog. The local browser uses the scoped `dev-user-token`; keep both services bound to loopback and do not expose this prototype credential to a shared network.

For a presentation build on one origin with a fresh, temporary wallet and signing key:

```bash
just demo
```

Open the local URL printed by the command. Stopping it removes only that temporary wallet. To keep demo records across restarts, set `MANDATE_DEMO_DATA_DIR` to a dedicated local directory first. See [the frontend runbook](apps/web/README.md) and [`just --list`](justfile) for more commands.

If `just` is unavailable, install only the demo runtime and launch directly:

```bash
uv venv .venv --allow-existing
uv pip install --python .venv/bin/python -r services/api/requirements-wallet.txt
npm ci --prefix apps/web
npm run build --prefix apps/web
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/run-demo.sh
```

This starts a fresh, disposable local wallet and serves the built web app and API from one loopback origin. See [Noah's detailed demo runbook](docs/noah-demo-runbook.md) for the presentation and reset sequence.

## Current integration state

- **Wallet:** mandate confirmation/revocation, quote calculation, atomic reservations, payment-route comparison, one-time approval and sandbox receipt are connected to the UI.
- **Catalog:** the API and UI use a timestamped Wellcome capture with source evidence. It is an observed snapshot, not a live retailer connection. Run `just capture` to create a new capture; review it before committing.
- **Shopping agent:** `POST /agent-runs` is mounted. Jev (TypeSafe's choice model) picks one catalog product per shopping-list item, or none, from the products the mandate allows. It never sees blocked categories and never writes prices. The wallet quotes the basket as the mandate's agent, and the user reviews it and checks out through the wallet. Auto-purchase is refused. Runs are kept in memory. `TYPESAFE_API_KEY` must be in the environment or the repository `.env`. Natural-language mandate drafting (`POST /mandates/draft`) is still not mounted.
- **Audit and formal verification:** the UI has the contract-backed views, but Seungbin's audit/checkpoint/verifier/Z3 services are not mounted yet; no passing result is fabricated.
- **Pickup-price limit:** only free Click & Collect above HK$50 is evidenced. The client blocks smaller Wellcome baskets, and `POST /quotes` also refuses them with `422 INVALID_REQUEST` (`details.reason_code = SUBTOTAL_NOT_SUPPORTED`) because no observed fee rule covers that subtotal.

## Project documents

- [API contracts](contracts/README.md)
- [Noah’s frontend and end-to-end integration plan](docs/frontend-implementation-plan.md)
- [Noah’s local demo and reset runbook](docs/noah-demo-runbook.md)
- [Overall product and technical plan](docs/mandate-build-plan.md)
- [Observed catalog capture and evidence notes](services/agent/README.md)
