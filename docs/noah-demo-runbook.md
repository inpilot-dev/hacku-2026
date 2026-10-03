# Noah — Local demo runbook

This runbook covers the integrated shopping, wallet and safety flows on `main`. The presentation uses a local payment simulator; **no real payment is made**. Use the fresh ephemeral data directory created by `just demo` so each rehearsal starts with no mandate or receipts.

A recorded 25.6-second walkthrough is available at [docs/demo/mandate-demo.mp4](demo/mandate-demo.mp4), with its recording-time limitations in [docs/demo/README.md](demo/README.md). It predates the current live agent, audit and Safety Lab integrations; use the live flow below to demonstrate those features.

## Start and reset

From the repository root:

```bash
just install    # first run only
just demo       # builds the UI, then starts a fresh single-origin wallet demo
```

Open the loopback URL printed by the command. Stop with Ctrl-C. The temporary wallet database and signing key are removed when the process exits. Run `just demo` again for a clean rehearsal. Avoid setting `MANDATE_DEMO_DATA_DIR` for a judging run unless you explicitly want persistent demo state.

The captured Wellcome catalog is the default. Its current snapshot is timestamped in the UI; it is not a live price feed. Product availability and promotions may change. Re-capture with `just capture`, review the evidence, then rebuild/rehearse if using a newer snapshot.

If `just` is not installed, the demo can be prepared and launched directly from the repository root:

```bash
uv venv .venv --allow-existing
uv pip install --python .venv/bin/python -r services/api/requirements-wallet.txt -r services/api/requirements-verification.txt
npm ci --prefix apps/web
npm run build --prefix apps/web
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/run-demo.sh
```

The launcher still creates and removes a fresh temporary wallet on exit.

## Main presentation flow

1. **Show the bounds.** Choose **Set up family spending** and review the sample mandate: HK$300 per order, HK$800 per week, Wellcome Click & Collect only, no alcohol, expiry 31 October 2026. The draft endpoint uses a deterministic rule parser, not an LLM; unresolved fields become questions. Review and explicitly confirm the proposed rules.
2. **Show real catalog evidence.** Open **Shopping**. The page should say **Observed price snapshot**, show Wellcome Click & Collect, display 80 products for the committed capture, and link each listed price to a timestamped source. Search and category filters narrow the list without changing the basket.
3. **Quote the basket.** The preselected basket uses the captured first available produce and pantry products. In the current snapshot that is Jumbo Gold Kiwifruit (HK$11) and Golden Elephant Premium Jasmine Rice 8KG (HK$89.90), total HK$100.90. Choose **Check against family rules**. The wallet returns the quote and the Click & Collect fee evidence.
4. **Compare payment routes.** Show the routes returned for that quote and their fees, rewards, caveats and sources. The route recommendation is advisory; the payment call still runs through wallet validation. Rewards and fees are simulated in this local demo.
5. **Complete one sandbox purchase.** Choose **Complete sandbox purchase**. Show the saved receipt, payment route and weekly budget refresh. State clearly that the amount is simulated and no money moved.
6. **Optional one-time approval scene.** For a separate clean rehearsal, set **Require approval above** to HK$80 before confirming the mandate. The HK$100.90 basket should pause for owner review; choose **Approve once**. The wallet rechecks the rules, completes the same transaction, and replaces the pending review with the receipt.
6a. **Optional unusual-purchase review.** Turn on **Review unusual purchases** before activating a fresh mandate. Its first order pauses with a concrete first-purchase reason; approve once and show that the same basket then completes. The approval covers only the reasons and basket shown. This opt-in feature uses explicit heuristics, not a general fraud score. Listing text that tries to direct the shopping agent is always sent for review, even when the opt-in is off.
7. **Demonstrate a policy refusal.** Adjust the basket: remove the rice and add one captured alcohol product priced below HK$300, such as Blue Girl Imported Premium Beer 4x500ML in the current catalog (SKU `wellcome_101326603`, HK$51). Request a fresh quote, then attempt checkout. The wallet should return the alcohol-rule refusal and no second receipt. The exact price may change after recapture; choose an available alcohol item whose total remains within the current per-order cap.
8. **Close with revocation.** Return to the overview/wallet, revoke the mandate, and show its persisted revoked status. Do not imply this reverses the completed earlier sandbox receipt.
9. **Show the technical proof.** In the one-screen app, expand **How this works** and open **Open the full dashboard**. Choose **Activity & safety**. The live event feed should show the purchase. Create a checkpoint, verify the history (valid), then choose **Tamper & verify** (invalid with a hash mismatch). Run **Run unsafe vs atomic models** and show the unsafe counterexample beside the atomic bounded result. Explain that the measured HTTP comparison is a saved evaluation result, while Z3 runs live with an explicit bound and assumptions.

If you start a fresh order after a receipt, the active order panel clears the prior checkout result. The saved receipt remains in the wallet history; it is not presented as the outcome of the new basket.

A clean-ledger browser rehearsal on 3 October confirmed the refusal scene with the captured Blue Girl beer at HK$51: the wallet returned the blocked-category refusal and no receipt. Revoking the mandate then made the shopping inputs and quote action read-only/disabled.

## Evidence boundaries to say aloud

- Prices are observations from the capture timestamp, not live checkout prices.
- Only Wellcome Click & Collect is included. The wallet enforces the observed pickup-fee coverage rule server-side; baskets below the evidenced HK$50 threshold cannot be quoted. The catalog is a timestamped snapshot, not a live retailer feed.
- The draft parser is deterministic and the user confirms the explicit resulting rules. Shopping runs use Jev when configured; if Jev is unavailable, the one-screen UI may use its clearly labelled preset basket and still sends the basket through the wallet quote and policy checks.
- **Review unusual purchases** is an opt-in mandate rule. It can pause the first purchase, a basket at least 3× the median after three paid purchases, new products after that history exists, or a product with a unit price at least 25% above the last paid price. Agent-targeting listing text is always escalated. Hard limits remain refusals and cannot be approved. No funds are reserved while an approval is pending; at most three pending requests can wait per mandate.
- The event feed, independent checkpoint verifier, tamper check, and bounded Z3 model are live in **Activity & safety**. The measured concurrent-request comparison is loaded from `evaluation/results/latest.json`, with its evaluation timestamp, source commit and artificial delay shown; it is not a fresh browser-triggered race. A bounded Z3 result applies only to the stated model and bound.
- The payment rail is local simulation. Route rewards are evidence-based estimates and do not move money.
- Checkout responses contain the wallet decision and receipt only; the signed authorization token and single-use payment credential stay server-side.

## Backup run

If the observed catalog API or saved evidence is unavailable, the app falls back to a visibly labeled placeholder catalog; do not show placeholder prices as retailer evidence. Keep the UI build and local server on the main machine, and rehearse the same start/reset sequence on the backup machine before the event. If Jev credentials or connectivity are unavailable, use the labelled preset basket. Do not describe the preset as an AI-selected basket.

On 3 October, the current main commit was also checked from a separate temporary Git worktree with a fresh Python runtime environment and `npm ci`. The frontend built and the single-origin demo served `/`, `/api/v1/health`, and the authenticated captured catalog route. The temporary worktree and demo ledger were removed after shutdown. This confirms a clean-checkout recovery path on this machine; a second physical machine still needs its own rehearsal.

The 390×844 mobile browser rehearsal also completed the mandate, quote, sandbox receipt, alcohol refusal and revoke scenes. It uncovered and fixed quantity buttons remaining active after revocation. After the production rebuild, all quantity controls and checkout actions were disabled against the revoked wallet, with no horizontal overflow. Rehearse the same sequence on the backup computer before submission.

The additional responsive sweep found no horizontal overflow in Overview, Shopping, Family wallet or Activity & safety at 390px. The mandate-review dialog scrolls internally and keeps **Activate these rules** reachable at its bottom.

## Current integration verification — 3 October 2026

After merging Seungbin PRs #9 and #11, a disposable checkout containing the merged code passed the Vite production build, all 108 API tests, and all 3 evaluation tests. The evaluation tests require the repository-local `services/api/.venv/bin/python` path; the isolated verification mapped that path to its temporary environment. The single-origin demo served the built SPA, `/api/v1/health`, the authenticated 80-product catalog and the live verification endpoint with HTTP 200.

A live browser rehearsal activated the mandate, packed the preset basket because Jev credentials were not configured in the isolated environment, and completed a sandbox purchase for HK$151.80. The wallet showed the updated HK$648.20 remaining budget and real audit events. A fresh quote containing Blue Girl beer was blocked by the wallet; revocation then changed the mandate to revoked and removed its spending authority. In **Activity & safety**, checkpoint creation succeeded; normal verification returned **valid through checkpoint**; tamper verification returned **HASH_MISMATCH**; and the live Z3 calls returned an unsafe counterexample and **no counterexample within bound** for the atomic model. The panel also displayed the measured HTTP baseline and wallet outcomes from the timestamped evaluation artifact. This is an end-to-end check of the merged local prototype, not a real retailer purchase or a proof beyond the stated Z3 model.

The prior note about disconnected agent and audit/verification APIs is superseded by this checkpoint. The second physical-machine rehearsal remains outstanding. Seungbin PR #12 is still a WIP evaluation extension and is not required for the current Safety Lab path.

## Risk review rehearsal — 3 October 2026

The current UI includes Timmy's merged opt-in **Review unusual purchases** mandate rule. Start a fresh demo, enable it before confirming the mandate, activate, then quote the preset basket. The initial purchase should pause with the first-purchase reason. Show that the pending state has no reservation or payment, inspect the exact reason, and select **Approve once**. The same sandbox transaction should finish and update the remaining balance. Approval applies once to that basket and the listed review reasons only; it does not override hard limits. Listing text that tries to instruct the agent is always escalated.

After integrating Timmy PR #13, the frontend production build passed and all 140 API tests passed. The live browser rehearsal completed the review-and-approve flow for HK$151.80, with HK$648.20 remaining. No real funds moved. The second physical-machine rehearsal remains outstanding.


## Typed and spoken shopping-list flow — 3 October 2026

The latest UI accepts a typed list or a short microphone recording. Audio transcription runs locally through faster-whisper; the resulting words appear in the editable list. Parsing may use the configured OpenRouter model, with a clearly identified fixed-rule fallback when the provider is unavailable. Read the recognized item names and quantities to the audience before packing. Jev chooses only from the allowed catalog, and the wallet calculates totals and applies the confirmed mandate. If the agent is unavailable or refuses items, the UI reports that result; it does not silently turn a typed request into a different basket.

A first purchase from a shop not present in the owner's purchase history can now trigger wallet review alongside the other unusual-purchase reasons. The exact returned reasons are shown together in the review screen; approval applies only once to that basket and reason set.

After syncing these team changes, the frontend production build passed and all 157 API tests passed. Microphone capture and external model-provider round trips were not part of this local validation. A generated speech sample was subsequently exercised through the local transcription API: faster-whisper recognized “Two litres of milk and three apples” and the no-key fixed-rule parser returned two items with quantities 2 and 3. The authenticated transcription and parse endpoints both returned HTTP 200. This does not verify browser microphone permission UX or Jev packing.


## Fresh setup rehearsal — 3 October 2026

The documented direct setup fallback was exercised on the current Mac: create/reuse `.venv` with `uv`, install the API, verification, agent and wallet dependencies, run `npm ci`, then build and launch `scripts/run-demo.sh` with `PYTHON_BIN` set to `.venv/bin/python`. The single-origin SPA loaded in a browser; health and catalog returned HTTP 200. The demo stopped normally and removed its ephemeral ledger. This machine lacks the `just` command, so `just install` / `just demo` themselves could not be run here; their documented direct commands succeeded. Rehearse this sequence on the backup computer before submission.

The repository's complete test recipe passed after the latest sync: 157 API tests, 5 MCP wallet tests and 30 agent tests. The frontend production build passed; `npm ci` reported no vulnerabilities. Starlette emitted its existing `httpx` TestClient deprecation warning.
