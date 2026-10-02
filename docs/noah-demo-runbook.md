# Noah — Local demo runbook

This runbook covers the working frontend/wallet path. The presentation uses a local payment simulator; **no real payment is made**. Use the fresh ephemeral data directory created by `just demo` so each rehearsal starts with no mandate or receipts.

## Start and reset

From the repository root:

```bash
just install    # first run only
just demo       # builds the UI, then starts a fresh single-origin wallet demo
```

Open the loopback URL printed by the command. Stop with Ctrl-C. The temporary wallet database and signing key are removed when the process exits. Run `just demo` again for a clean rehearsal. Avoid setting `MANDATE_DEMO_DATA_DIR` for a judging run unless you explicitly want persistent demo state.

The captured Wellcome catalog is the default. Its current snapshot is timestamped in the UI; it is not a live price feed. Product availability and promotions may change. Re-capture with `just capture`, review the evidence, then rebuild/rehearse if using a newer snapshot.

## Main presentation flow

1. **Show the bounds.** Choose **Set up family spending** and review the sample mandate: HK$300 per order, HK$800 per week, Wellcome Click & Collect only, no alcohol, expiry 31 October 2026. Confirm the structured rules. Natural-language interpretation is not connected in this checkout, so do not present the draft text as model-produced.
2. **Show real catalog evidence.** Open **Shopping**. The page should say **Observed price snapshot**, show Wellcome Click & Collect, display 80 products for the committed capture, and link each listed price to a timestamped source. Search and category filters narrow the list without changing the basket.
3. **Quote the basket.** The preselected basket uses the captured first available produce and pantry products. In the current snapshot that is Jumbo Gold Kiwifruit (HK$11) and Golden Elephant Premium Jasmine Rice 8KG (HK$89.90), total HK$100.90. Choose **Check against family rules**. The wallet returns the quote and the Click & Collect fee evidence.
4. **Compare payment routes.** Show the routes returned for that quote and their fees, rewards, caveats and sources. The route recommendation is advisory; the payment call still runs through wallet validation. Rewards and fees are simulated in this local demo.
5. **Complete one sandbox purchase.** Choose **Complete sandbox purchase**. Show the saved receipt, payment route and weekly budget refresh. State clearly that the amount is simulated and no money moved.
6. **Demonstrate a policy refusal.** Adjust the basket: remove the rice and add one captured alcohol product priced below HK$300, such as Blue Girl Imported Premium Beer 4x500ML in the current catalog (SKU `wellcome_101326603`, HK$51). Request a fresh quote, then attempt checkout. The wallet should return the alcohol-rule refusal and no second receipt. The exact price may change after recapture; choose an available alcohol item whose total remains within the current per-order cap.
7. **Close with revocation.** Return to the overview/wallet, revoke the mandate, and show its persisted revoked status. Do not imply this reverses the completed earlier sandbox receipt.

## Evidence boundaries to say aloud

- Prices are observations from the capture timestamp, not live checkout prices.
- Only Wellcome Click & Collect is included. Captured evidence confirms free pickup above HK$50. The UI blocks lower baskets because their pickup charge is unknown; a server-side catalog rule is still needed to enforce that limit against direct API callers.
- The online shopping agent/draft API is not mounted in this checkout. Manual catalog selection is the working path; the agent button reports the unavailable service.
- Activity feed, independent audit verification, bounded Z3 results and measured concurrent-agent results remain disconnected. Show their “not connected” state rather than claiming a pass.
- The payment rail is local simulation. Route rewards are evidence-based estimates and do not move money.

## Backup run

If the observed catalog API or saved evidence is unavailable, the app falls back to a visibly labeled placeholder catalog; do not show placeholder prices as retailer evidence. Keep the UI build and local server on the main machine, and rehearse the same start/reset sequence on the backup machine before the event. If an external teammate module is still absent, use the manual catalog path and the actual wallet refusal/receipt flow; do not fabricate the missing agent, audit or solver output.
