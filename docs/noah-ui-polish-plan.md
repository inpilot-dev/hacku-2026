# Noah: product UI, receipts and character motion

Planning checkpoint: 3 October 2026, source main `449c48d`.
Status: proposal only; no product code changed by this plan.

## Direction and current evidence

Use Timmy's default `SimpleApp` as the product design foundation: generous white space, large type, rounded cards, restrained borders, black primary actions, and colourful tactile characters. Keep a readable, calm canvas and put colour into characters, grocery illustrations and meaningful state changes.

`apps/web/src/main.tsx` currently selects the simple experience at `/` and the detailed dashboard at `?classic`. Treat these as the consumer experience and technical evidence view. Keep the dashboard reachable through a clearly labelled evidence link while bringing everyday receipt history into the simple experience.

The source and saved `docs/ui-redesign/before-after-desktop.png` were inspected. Live browser inspection was blocked by the browser URL policy in this planning turn; the saved image predates typed/voice shopping. Current source is the authority for available features.

Existing character assets include Kumi (shopping bag), Kip (wallet), Bean (request interpretation), and Stella (audit). They have transparent PNGs and multiple expressions. `source/agents.html` contains a Three.js scene that renders a static frame. The simple UI changes expressions by outcome. Character movement is paused in this release; only the existing non-character loading/status effects remain.

## Today's ownership

- Noah: edit presets; digital receipts and previous receipt access; product interaction polish and shared integration.
- Abdullah: additional stores and actual merchant-cart integration.
- Timmy: virtual card and wallet/payment behaviour.
- Seungbin: mobile responsiveness, currently proposed in PR #18.

Review incoming team changes before integration and before push. Coordinate edits to shared `SimpleApp` and CSS with the mobile branch. Show only the control card network and last four digits; explain that the agent never sees reusable card details and that checkout card rails use single-use cards. Keep sandbox payment limits visible.

## 1. Editable presets — first deliverable

Each normal preset has separate **Edit** and **Shop** actions. Edit opens a sheet in the existing visual language with a name, item names, quantities and units. Users can add/remove items, save, cancel, duplicate and reset to the default. The policy-rule example is visibly identified as a demo scenario.

Save personal presets with a versioned local-storage schema and stable IDs. Describe them as saved on this device. Validate nonempty names, bounded list length and the API's quantity range. Invalid or old saved data recovers to usable defaults without silently overwriting a valid saved preset.

Model presets as shopping intent (`ShoppingItem[]`). A separate catalog-bound basket may be used for explicitly selected products. Edits must affect the exact list sent to the agent. An edited preset must never fall back to the old default basket when the agent is unavailable; show the unavailable result and offer deliberate manual product selection.

The edit sheet can show captured catalog estimates where products are explicitly selected. Mark estimates as estimates. The wallet quote supplies the checkout amount, fees and allowed actions. Canceling an edit leaves the original preset intact.

Completion evidence: edit quantities and names, save/reload, cancel/reset, verify the outbound agent request uses the edited list, and verify a missing agent cannot cause the original preset to be purchased.

## 2. Receipt library — second deliverable

Add **Receipts** near the wallet and a compact latest-receipt preview. Selecting a record opens a receipt detail sheet styled as a paper slip: merchant, paid timestamp, line items, charges, total, payment route and transaction reference. Add print/download through a print stylesheet; the receipt clearly says sandbox when the backend says sandbox.

The receipt already contains `transaction_id`, `quote_id`, `mandate_id`, merchant, amount, currency, paid timestamp and payment route. It does not contain line items. Load the related quote for the immutable purchased basket and fees.

There is no receipt-list route on current main. `GET /audit/export` returns the authenticated owner's audit stream; completed-payment events contain the signed transaction reference and receipt. The UI deduplicates transactions and reads receipt records from those events (falling back to `GET /payments/{transaction_id}` if a receipt payload is missing). It fetches item details from `GET /quotes/{quote_id}` only when a receipt is opened. This keeps the list complete without one request per purchase. If audit streams become large, propose a wallet-owned paginated receipt-list contract to Timmy.

Receipt history is reloaded from the authenticated backend, survives page reload and new orders, and includes prior mandates in that owner's stream. The latest paid purchase also appears on the wallet card. Quotes are loaded on demand for each opened record. A new ephemeral demo ledger has no previous receipts; stale local IDs should not appear as successfully verified purchases.

Provide loading, empty, unavailable, partial-details and retry states. A paid receipt remains accessible if its quote details fail to load; show that detail gap. Refused or pending orders have their own states and are not paid receipts. Replayed checkout results create one history record per transaction. Audit verification is shown only when an independent checkpoint verifier actually returns it.

Completion evidence: buy twice in the sandbox, reload, open each old receipt, change the active allowance, confirm history remains discoverable, replay an existing transaction without duplicates, and reset the demo ledger without showing stale paid records.

## 3. Visual composition

Keep the desktop composition recognizable: allowance/rules on the left, shopping on the right, and a compact activity/receipt area. Use a focused sheet for edits and receipt details rather than expanding every control onto the landing screen. Small screens follow Seungbin's mobile layout and accessible touch targets.

Build a small illustrated shopping scene inside Kumi's panel: an anchored bag, a shelf or soft ground shadow, and a few product silhouettes from the actual basket. Use existing character colours as accents: orange for shopping, green for wallet, blue for interpretation, pink for audit. Prioritize high-quality character placement and scale over filling the background with decoration.

Make the wallet surface more tactile through a restrained highlight, a clear segmented budget meter and a deliberate character anchor. Its displayed balance comes from the backend. Receipt paper gets a subtle edge, sensible amount hierarchy and a paid mark triggered only by the backend receipt.

Extract reusable components as the features land: `PresetCard`, `PresetEditor`, `ReceiptList`, `ReceiptDetail`, `Character` and `ShoppingStage`. Reuse API types and token values. Existing dashboard functions remain available during the transition.

## 4. Motion tied to the workflow

| State | Character/graphic | Motion and meaning |
|---|---|---|
| Ready | Kumi, Kip, Stella | A gentle 3-second breathing/float loop on the focused character |
| Interpreting a list | Bean | Thinking pose with restrained thought dots |
| Building a basket | Kumi | Small lean/bounce; product chips enter as selection becomes known |
| Basket ready | Kumi | Happy pose; sheet enters and item rows reveal once |
| Wallet checking | Kip | Checking pose with an active progress cue |
| Owner review | Bean/Kip | An attentive pose; review reasons stay steady and readable |
| Paid | Kip | One short happy bounce; paid stamp and receipt reveal after success |
| Refused | Kip | One restrained shake, refused expression and visible explanation |
| Frozen | Kip | Settles into sleeping pose; spending controls become inactive |
| Audit result | Stella | Pass/fail expression only after the corresponding verifier result |

Use CSS transforms and opacity for the first version: roughly 120–160ms for button feedback, 180–260ms for sheets and panels, and 450–700ms for a single outcome animation. Motion must not delay actions or imply a successful payment while a request is pending. Keep uncertain results visually distinct and preserve transaction reconciliation.

Start with the existing PNGs and expression crossfades. Position effects in separate layers so character tilt, idle movement and button feedback do not overwrite each other's transforms. Respect `prefers-reduced-motion`, keyboard focus and offscreen/hidden-page pause behaviour.

For more lifelike character motion, render short transparent sprite sequences or animated assets from the existing Three.js source: blink, bag wobble, wallet-open gesture, star spin. Load advanced assets only when the character is needed. The initial runtime stays lightweight; a full fur-rendering WebGL scene is a later option after mobile frame-rate and loading measurements.

## Implementation order and integration gates

1. Land editable presets with save/reload and correct agent inputs.
2. Land persistent receipt discovery, detail sheets and print styling.
3. Extract the shared character/state presentation and add the shopping scene.
4. Add state-specific motion, reduced-motion behaviour and coherent transitions.
5. Integrate Seungbin's mobile work; verify edit/receipt sheets and long titles at 390px and 320px.
6. Rehearse setup → edit preset → live Jev quote → wallet review/payment → old receipt → refusal → freeze → technical evidence.

Commit and push coherent stages. Fetch/review team work at each checkpoint. Keep the live Jev and wallet response authoritative, and keep the sandbox declaration visible. Build after product changes; run focused persistence/state checks and browser flows appropriate to the feature. Capture a fresh short recording once the integrated UI is stable.

Planning estimates, subject to incoming team changes: presets 1–2 hours; receipt library 2–3 hours; visual/motion pass 1–2 hours; integration and final rehearsal about 1 hour. Complete the two assigned functional deliverables before spending the remaining window on advanced animated assets.
