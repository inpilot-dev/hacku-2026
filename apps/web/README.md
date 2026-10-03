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
- Wellcome's captured evidence establishes free Click & Collect only for orders over HK$50. The UI blocks lower baskets, and the wallet also rejects quotes whose delivery fee is not covered by observed evidence. The capture is not a live price feed.
- `POST /api/v1/demo/purchases` is a local-only, user-triggered integration adapter. It derives the agent from the user's own mandate and calls the same wallet authorization/payment checks; it never sends the signing capability or agent credential to the browser. It is disabled unless `MANDATE_ENABLE_DEMO_CHECKOUT=1`.
- `POST /api/v1/mandates/draft` is mounted and uses deterministic rule-based interpretation. It returns ambiguities instead of guessing; the draft remains inactive until the user reviews and confirms it. This is not an LLM-based mandate compiler.
- The shopping form uses the mounted agent-run start, progress and quote APIs, with `auto_purchase: false`. Jev selection requires `TYPESAFE_API_KEY`; runs are in memory and disappear when the API process restarts. The manual catalog flow remains available as a transparent fallback.
- `GET /api/v1/stores` and the Wellcome connection/cart-sync routes are mounted. The user must complete the retailer sign-in in the local Steel browser; a store connection has not been established just by starting the app. Cart sync adds items to the store cart but does not submit a payment.
- Audit/checkpoint/verifier and bounded Z3 routes are mounted. The independent verifier is a separate process on `:8201`, started by `just dev` / `just demo`; the UI reports unavailable services instead of presenting mock results as verified.
- The default payment rail is a local simulator. No real funds move.

## Serve the built site from the API

For a reproducible presentation run without the task runner, build with `npm --prefix apps/web run build`, then invoke `./scripts/run-demo.sh` from the repository root with the project Python interpreter. It starts the same-origin frontend and API and uses the captured catalog unless `MANDATE_CATALOG_PATH` is set explicitly.

## App layout (tabs)

The default page is a tabbed app built with shadcn/ui on Tailwind v4 (`src/shell/`, components in `src/components/ui/`):

- **Buy**: one-time purchases from a typed request (`/purchases`). The agent searches the web, checks out as a guest and shows the exact total; nothing is paid until you approve it. Shops that need an account come back as links.
- **Groceries**: repeat shopping under the allowance (`/agent-runs`, quotes, sandbox checkout, approvals, cart sync, presets, voice lists).
- **Wallet**: the allowance and its virtual card (freeze, change rules, revoke), receipts, delivery details for the Buy agent (`/profile`), store accounts and activity.

The current tab is kept in the URL hash (`#buy`, `#groceries`, `#wallet`). Tailwind is imported only by the shell's chunk, so `?classic` (full dashboard), `?card`, `?about` and `?security` keep their own styles. The previous single-page flow is still available at `?legacy`. Add shadcn components with `npx shadcn@latest add <name>` from `apps/web`.

## Conversational shopping UI (`?legacy`)

The previous single-page flow presents user requests and real agent progress in a chronological conversation. Existing quote, approval, sandbox payment, cart-sync and receipt controls are rendered in the active conversation card. Enter submits a request; Shift+Enter adds a line. Agent messages are based on the run API rather than simulated milestones. The transcript is session memory and resets on a full reload; receipts remain recoverable from the wallet.

Setup progresses through draft rules, store selection/sign-in, and an explicit review of the exact policy before confirmation. The expiry shown during review is the expiry submitted to the wallet. Existing mobile, profile, card and classic audit routes are retained. The responsive conversation layout shows the conversation first on phones. Retailer cart sync and sandbox payment remain separate operations; simulated payment is not retailer order confirmation.

Browser checks: setup rules → stores → exact permission review; 390px viewport with no horizontal overflow; blocked alcohol/over-budget request returning wallet refusals without a debit; normal HK$113.80 sandbox checkout using a user-selected Tap & Go route, receipt and budget update. These checks did not sign in to a retailer or submit a retailer payment.

Payment route cards display the wallet’s fee, estimated reward and net-cost comparison with observed sources and caveats. The frontend does not recalculate financial values. A completed purchase links directly to the digital receipt. Receipt refresh preserves already-loaded quote details.

The digital receipt was opened from the checkout result and from history, then history was refreshed and the item details were confirmed to remain visible. Approval and uncertain-payment states were subsequently exercised in an isolated wallet; see the verification notes below. Structured site-risk cards depend on a shared API response; the current store connection messages are displayed as text without inferring risk scores.

Uncertain checkout handling: HTTP failures never imply a refusal. The client attempts a receipt lookup, preserves an unresolved transaction reference in sessionStorage, and restores its quote/receipt on reload without submitting a payment. The “Check payment status” action is GET-only. A missing receipt leaves the result unknown, and a new shopping flow is unavailable while unresolved. Quote-restore failure also blocks a fresh purchase. Known wallet refusals and completed receipts clear the pending reference. Response-loss behavior was reproduced in an isolated runtime; see the verification notes below.

### Isolated runtime verification (3 October 2026)

A temporary server on :8016 used its own wallet, keys and store data under /tmp. The user-facing :8000 wallet was not modified by these checks.

- Activated a permission through rules → stores → exact policy review, with a HK$100 approval threshold.
- A HK$139.30 purchase requested approval; denying it left the payments table empty and the HK$800 budget intact.
- A deliberate new shopping request generated a new run, rather than reusing the denied purchase.
- Approved the next HK$139.30 purchase. A temporary middleware discarded the successful checkout response and returned HTTP 503; it also temporarily blocked receipt lookup. The UI showed an unknown result and prevented a new purchase.
- Reload preserved the unresolved quote and transaction. A failed status lookup kept the result unknown. Database counts remained one wallet payment and one rail payment.
- Restoring receipt lookup and clicking Check payment status recovered the original paid receipt. Counts remained one wallet payment and one rail payment; the available budget was HK$660.70.

The middleware is a temporary reproduction fixture, not product code. These checks cover response loss after a completed sandbox capture; they do not establish behavior for every possible real payment-network failure.
