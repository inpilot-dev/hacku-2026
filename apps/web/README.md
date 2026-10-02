# Mandate web prototype

React + TypeScript + Vite client for Noah's HacKU 2026 integration role. It consumes `../../contracts/types.ts` and keeps the wallet as the authority for policy decisions and basket totals.

## Run locally

Terminal 1:

```bash
cd services/api
MANDATE_ENABLE_DEMO_CHECKOUT=1 uvicorn mandate.app:app --reload --port 8000
```

Terminal 2:

```bash
cd apps/web
npm install
npm run dev
```

Open the URL printed by Vite. The browser uses the scoped local `dev-user-token` by default. Do not expose the prototype API or dev credentials on a shared network. To reset the local wallet state, stop the API and remove `services/api/.data/wallet/`.

## Current honest limitations

- The bundled catalog is explicitly placeholder data. The UI labels its prices as prototype data and asks the wallet service to calculate the actual quote.
- `POST /api/v1/demo/purchases` is a local-only, user-triggered integration adapter. It derives the agent from the user's own mandate and calls the same wallet authorization/payment checks; it never sends the signing capability or agent credential to the browser. It is disabled unless `MANDATE_ENABLE_DEMO_CHECKOUT=1`.
- The default payment rail is a local simulator. No real funds move.
- Draft interpretation, catalog API, agent shopping runs, independent audit verification and bounded Z3 evaluation are not implemented in the current checkout. The UI displays these limitations and is structured to replace them with the agreed APIs.

## Serve the built site from the API

For a single-origin presentation build, run `npm install && npm run build` in `apps/web`, then start the API command above. FastAPI serves the built SPA and its assets at `http://127.0.0.1:8000/`; Vite proxying is only needed during frontend development. Unknown `/api/...` paths remain JSON 404 responses rather than falling through to the SPA.
