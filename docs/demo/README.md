# Mandate demo recording

`mandate-demo.mp4` is a 25.6-second screen recording of the local Mandate prototype.

## Walkthrough

1. The family owner reviews and activates a weekly spending mandate.
2. The shopper checks a Wellcome basket against the mandate and completes a sandbox purchase. The receipt is HK$100.90.
3. The basket is changed to a prohibited beer product. The wallet refuses the attempted purchase.
4. The owner revokes spending access and the wallet view updates.
5. The Activity & Safety view shows its real disconnected-service state.

The recording uses the disposable local wallet sandbox launched by `scripts/run-demo.sh`. It does not use a real payment card, transfer real money, or place a real order. The catalog snapshot shown was captured on 3 October 2026 and may no longer reflect current store prices or offers.

The agent shopping-run service and event/audit APIs were not available in this checkout during recording. The video demonstrates the frontend-to-wallet purchase, refusal, and revocation flow; it does not claim a live autonomous browser-shopping run or a working audit verifier.

The current app also offers an opt-in exact-title scripted catalog matcher when the agent-run endpoint is unavailable. That separate fallback was verified in the browser but is not shown in this recording; it performs no model inference.
