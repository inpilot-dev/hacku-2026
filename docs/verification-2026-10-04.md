# Shopping flow review — 4 October 2026

## Results

- Latest wallet web-card changes from main integrated with frontend outcome fixes.
- API suite: 282 passed, one existing Starlette/httpx deprecation warning.
- Frontend: TypeScript and Vite production build passed.
- Running API `/api/v1/health`: healthy, payment mode sandbox.
- Steel `/v1/health`: healthy. This is a service health check, not proof of CDP or retailer checkout success.
- Actual app rendered, with no captured browser console errors at the initial check.
- UI fixture checked at 320px mobile and 1280px desktop, dark and light views. No horizontal overflow in checked mobile search, error, cart and receipt states.

## UI flows checked with an isolated synthetic API

Search progress and failure; approval card and one approval request; selected-item quote; confirmed store cart; unconfirmed payment with no receipt; recovery from an existing receipt without resubmitting payment; declined payment with no receipt; printed receipt and detail dialog. The fixture made two checkout requests for two distinct scenarios; status recovery did not create a third request. No real retailer order or payment was submitted.

## Fixed

- Buy header now uses the latest purchase, prioritizes failure and excludes cancelled requests from success poses; historic purchases cannot keep it smiling during the current failure.
- Grocery companion distinguishes payment in progress, review, refusal, unknown outcome and confirmed sandbox payment instead of continuing to celebrate a priced basket.

## Remaining blockers and limits

- A real search for a generic 65W charger was rejected by DuckDuckGo HTML with a browser verification challenge. The application reports this correctly. Actual web search through checkout remains blocked; a reliable supported search provider is needed.
- The actual grocery card is frozen. It was left frozen; no allowance or payment permissions were changed during this review.
- Model credentials/authentication, CDP handshake, retailer guest checkout and live payment were not end-to-end verified because actual search stopped at its provider boundary.
- Physical mobile keyboard behavior was not checked. Viewport resizing verifies responsive layout, not a real phone keyboard.
- Synthetic approval/payment/cart responses validate frontend behavior only, not real merchant integration.
