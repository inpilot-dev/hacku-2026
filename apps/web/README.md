# Mandate web prototype

React + TypeScript + Vite client for Noah's HacKU 2026 integration role. It consumes `../../contracts/types.ts` and keeps the wallet as the authority for policy decisions and basket totals.

## Run locally with the observed Wellcome snapshot

Prerequisites: Python 3.12+, Node.js/npm, `uv` and the `just` command runner. From the repository root:

```bash
just install
```

Run the API and Vite client together:

```bash
just dev
```

Open `http://127.0.0.1:5173/`. The API uses the captured Wellcome catalog by default. The browser uses the scoped local `dev-user-token` by default. Both servers bind to loopback; do not expose the prototype API or dev credentials on a shared network.

For a single-origin presentation demo with a fresh temporary wallet:

```bash
just demo
```

Open the URL printed by the script (default `http://127.0.0.1:8000/`). Stopping the process removes the temporary sandbox database and key. To preserve demo state across restarts, set `MANDATE_DEMO_DATA_DIR` to a dedicated local directory before `just demo`; that directory is not cleared by the script. `MANDATE_DEMO_PORT` changes the port. Set `MANDATE_CATALOG_PATH` to override the captured catalog.

## Current honest limitations

- The default catalog is a timestamped Wellcome snapshot captured from public pages, not a live retailer connection. The UI shows the observation date, evidence links and the Click & Collect scope. The placeholder catalog is used only as a labeled fallback.
- Wellcome's captured evidence establishes free Click & Collect only for orders over HK$50. The UI blocks lower baskets, but the wallet catalog adapter still needs to reject unpriced delivery subtotals server-side; do not describe this client-side guard as an authorization boundary.
- `POST /api/v1/demo/purchases` is a local-only, user-triggered integration adapter. It derives the agent from the user's own mandate and calls the same wallet authorization/payment checks; it never sends the signing capability or agent credential to the browser. It is disabled unless `MANDATE_ENABLE_DEMO_CHECKOUT=1`.
- The mandate form can request a natural-language draft from the contract endpoint. The response remains inactive until the user reviews the structured fields and confirms it. If the draft endpoint is absent, the form explicitly falls back to the seeded structured policy.
- The shopping form uses the contract-defined agent run/start, progress and quote APIs, with `auto_purchase: false`. It leaves the manual catalog flow available when the agent-run backend is absent; do not treat the contract client as proof that the worker is deployed.
- The default payment rail is a local simulator. No real funds move.
- The observed catalog API and shared FastAPI entrypoint are implemented. Draft interpretation, agent shopping runs, independent audit verification and bounded Z3 evaluation are not mounted in the current checkout. The UI displays these limits and is structured to consume the agreed APIs when available.

## Serve the built site from the API

For a reproducible presentation run without the task runner, build with `npm --prefix apps/web run build`, then invoke `./scripts/run-demo.sh` from the repository root with the project Python interpreter. It starts the same-origin frontend and API and uses the captured catalog unless `MANDATE_CATALOG_PATH` is set explicitly.
