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

## Current integration state

- **Wallet:** mandate confirmation/revocation, quote calculation, atomic reservations, payment-route comparison, one-time approval and sandbox receipt are connected to the UI.
- **Catalog:** the API and UI use a timestamped Wellcome capture with source evidence. It is an observed snapshot, not a live retailer connection. Run `just capture` to create a new capture; review it before committing.
- **Shopping agent:** natural-language draft and shopping-run routes are not mounted yet. The UI leaves manual catalog shopping available and reports when those APIs are unavailable.
- **Audit and formal verification:** the UI has the contract-backed views, but Seungbin's audit/checkpoint/verifier/Z3 services are not mounted yet; no passing result is fabricated.
- **Pickup-price limit:** only free Click & Collect above HK$50 is evidenced. The client blocks smaller Wellcome baskets, but the wallet quote adapter still needs a server-side rule for unsupported subtotals before that restriction is enforced against direct API callers.

## Project documents

- [API contracts](contracts/README.md)
- [Noah’s frontend and end-to-end integration plan](docs/frontend-implementation-plan.md)
- [Noah’s local demo and reset runbook](docs/noah-demo-runbook.md)
- [Overall product and technical plan](docs/mandate-build-plan.md)
- [Observed catalog capture and evidence notes](services/agent/README.md)
