# Shopping agent (owner: Abdullah)

## Observed catalog capture

`catalog_capture` records real Wellcome.com.hk prices in the wallet's catalog file shape
(`services/api/mandate/payments/catalog.py`). It replaces the placeholder catalog with observed data.

```bash
just capture            # or: cd services/agent && ../../.venv/bin/python -m catalog_capture
just backend-observed   # API with MANDATE_CATALOG_PATH=data/catalog/wellcome.json
```

How a capture works:

- **Plain HTTP only.** Wellcome renders category pages on the server, so no browser, model or API key
  is involved. The capture fetches the English home page, page 1 of four categories and one product
  page per category, about 9 requests 2 s apart. `robots.txt` permits these paths. Prices come from fixed page
  elements, and the struck-through old price is read separately from the current price.
- **Cross-checked.** The first available listing in every category is compared with its product page's
  schema.org JSON-LD `Offer.price`. Any mismatch aborts the run, and nothing is written.
- **Evidence kept.** Every fetched page is saved gzipped under `data/catalog/evidence/wellcome-<stamp>/`.
  Each evidence record's `capture_path` points at the page its value came from, with the source URL, the
  observation time (HKT) and the conditions (guest session, no address, struck-through price, promotions
  not captured).
- **Categories are a curated mapping** from Wellcome's top-level categories (`wellcome.CATEGORIES`).
  - Rice, Oil & Noodles → `pantry`; Fruits & Vegetables → `produce`; Beverages → `beverage_non_alcoholic`.
  - Alcohol → `alcohol`, so a blocked-category refusal can be shown.
  - A product listed under two differently mapped categories, or a non-alcohol listing whose title looks
    alcoholic, is marked `conflicting`. A mandate that blocks alcohol then sends it to review.

The MCP wallet server's `get_catalog` tool needs an API that mounts this route. Point it at
`just backend-observed` (port 8000). `dev_app:seeded_app`, which the MCP README uses, does not mount `GET /catalog`.

Merchant ID is `wellcome`, and the delivery context is `ctx_wellcome_click_collect`. `GET /catalog` does not
expose delivery contexts, so a client must know this ID.

## Known gaps (not filled with guesses)

- **Home delivery is not offered.** Wellcome publishes "free delivery to your door on orders over HK$500",
  but the charge below HK$500 is not shown without a cart. Every order under the demo's HK$300 cap falls
  below that threshold, so only Click & Collect is captured.
- **Pickup orders of HK$50 or less.** The observed rule is "free Click & collect on orders over HK$50". The
  wallet's catalog adapter adds no charge when no fee rule matches, so a basket of HK$50 or less would be
  quoted with no pickup charge. Raising an error when no fee rule covers the subtotal is a wallet
  (`catalog.py`) change for Timmy.
- **Promotions** such as "Buy 2 for $30" exist only in the page's minified app state and are not
  captured. Prices are single-unit prices.
- **Page 1 only:** 20 products per category.
- **Other stores:**
  - HKTVmall redirects browser search and category pages to its login page.
  - ParknShop returns HTTP 403 (Access Denied) to both scripts and a normal browser session.
  - Neither is captured, and no access control is bypassed.

## Note for the frontend (Noah)

`GET /api/v1/catalog?merchant_id=...` now returns the real catalog the wallet quotes from. `App.tsx` still
hardcodes `demo_store_a`, `ctx_a_standard` and the `p_a_*` product IDs, and falls back to the bundled
placeholder JSON. To show observed data, the shopping screen needs to take its merchant, products and
evidence from `GET /catalog`, and the mandate form needs to allow `wellcome`.
