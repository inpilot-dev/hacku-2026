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

## Browser capture with Jev (store-independent)

`catalog_capture.browse` does not depend on any store's page layout. Jev ([browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast),
pinned in `requirements.txt`) drives a self-hosted Steel browser from the store's home page to a listing for
each shopping term. Generic code then opens same-site links whose text matches the term. A product is kept
only if its page publishes one HKD price in schema.org Product data (JSON-LD), and that price also appears in
the visible text.

```bash
just steel                                               # Steel browser; watch at http://localhost:3000/ui
just browse wellcome rice=pantry broccoli=produce milk=dairy
```

- **Needs** `TYPESAFE_API_KEY` and `OPENROUTER_KEY` in the repository `.env`. Output goes to
  `data/catalog/browse-<store>.json`, plus rendered product pages under `data/catalog/evidence/browse-<store>-<stamp>/`.
- **A store is configuration only** (`browse.STORES`): its start URL and the exact wording of any fee it
  publishes. A fee rule is added only when that wording appears on a captured page. Otherwise the store gets
  no delivery context, so its products can be listed but not quoted.
- **The category comes from the operator** per term (`rice=pantry`) and is recorded as such. Titles that look
  alcoholic ("Shaoxing Rice Wine" matches "rice") are marked `conflicting`.
- **Limits:**
  - A store whose product pages publish no JSON-LD price cannot use this path.
  - Jev cannot submit a search box, so a term works only if a clearly labelled category leads to it.
  - Roughly 10 s per product page.

First run, 2026-10-03, Wellcome:
- rice: 3 products, via Rice, Oil & Noodles.
- milk: 3 products, via Beverages.
- broccoli: Jev returned `blocked` on the home page and did not find Fruits & Vegetables.

## Known gaps (not filled with guesses)

- **Home delivery is not offered.** Wellcome publishes "free delivery to your door on orders over HK$500",
  but the charge below HK$500 is not shown without a cart. Every order under the demo's HK$300 cap falls
  below that threshold, so only Click & Collect is captured.
- **Pickup orders of HK$50 or less.** The observed rule is "free Click & collect on orders over HK$50"; the
  charge at or below HK$50 was not observed. The wallet therefore refuses such quotes (`422`,
  `SUBTOTAL_NOT_SUPPORTED`) because no observed fee rule covers them.
- **Promotions** such as "Buy 2 for $30" exist only in the page's minified app state and are not
  captured. Prices are single-unit prices.
- **Page 1 only:** 20 products per category.
- **Other stores:**
  - HKTVmall redirected browser search and category pages to its login page. Later its pages stopped
    finishing loading, in Steel and over plain HTTP alike (the response stalls partway). Whether it loads
    normally in an ordinary browser has not been checked.
  - ParknShop returns HTTP 403 (Access Denied) to both scripts and a normal browser session.
  - Neither is captured, and no access control is bypassed.

## Frontend integration status (Noah)

The React client now loads `GET /api/v1/catalog`, prefers captured `wellcome` products and evidence, uses
`ctx_wellcome_click_collect` for wallet quotes, and creates its sample mandate for `wellcome`. The bundled
placeholder catalog remains a fallback and is labelled as unverified. The normal `just dev` and `just demo`
recipes select `data/catalog/wellcome.json` unless `MANDATE_CATALOG_PATH` is explicitly set.

The UI blocks Wellcome quotes at or below HK$50 because the captured evidence only proves free Click & Collect
above that amount. The wallet also refuses those quotes server-side (`SUBTOTAL_NOT_SUPPORTED`).
