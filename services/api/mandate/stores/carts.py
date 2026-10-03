"""Fill the user's real store cart with a wallet quote (POST /carts/sync).

Adding to a cart moves no money, but it changes the user's real account, so:

- Only a store the user connected **and** the mandate allows is touched, and
  only while the mandate is active.
- The cart is read before any write, and each line is set to the quoted
  quantity with `beforeCount` = what the cart holds. A write whose answer is
  lost is never sent again: the cart is read back and the result reported.
- Nothing here checks out. The cart is then compared line by line with the
  quote. A shop price that differs from the quote, a missing line or other
  items the shop would include at checkout make the basket not checkout-ready.
  The cart total is evidence of what the shop will charge; it never replaces
  the wallet's quote.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

from mandate.payments.auth import Actor
from mandate.payments.clock import iso
from mandate.payments.errors import conflict, invalid
from mandate.payments.service import Wallet

from .connections import StoreConnections
from .registry import STORES
from .steel import StoreBrowserError


@dataclass(frozen=True)
class CartLine:
    sku: int
    title: str
    quantity: int
    unit_price_minor: int
    checked: bool


def parse_cart(body: dict) -> dict[int, CartLine]:
    """Lines of a superweb `cartInfo` answer by SKU. Reads product lines only, nothing about the account."""
    lines: dict[int, CartLine] = {}
    for group in (body.get("data") or {}).get("storeGroupList") or []:
        for store in group.get("storeList") or []:
            for item_group in store.get("itemGroupList") or []:
                for item in item_group.get("itemList") or []:
                    for ware in item.get("wareList") or []:
                        sku, count = ware.get("skuId"), ware.get("count")
                        if not isinstance(sku, int) or not isinstance(count, int):
                            continue
                        prior = lines.get(sku)
                        lines[sku] = CartLine(
                            sku=sku, title=str(ware.get("wareName") or sku),
                            quantity=count + (prior.quantity if prior else 0),
                            unit_price_minor=int(ware.get("unitSinglePrice") or 0),
                            checked=bool(ware.get("checked")) or bool(prior and prior.checked))
    return lines


class CartSync:
    def __init__(self, wallet: Wallet, connections: StoreConnections, browser):
        self.wallet = wallet
        self.connections = connections
        self.browser = browser
        self._locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock(self, key: tuple[str, str]) -> threading.Lock:
        with self._locks_guard:
            return self._locks[key]

    def sync(self, actor: Actor, mandate_id: str, quote_id: str) -> dict:
        mandate = self.wallet.get_mandate(actor, mandate_id)  # 404 unless the caller owns it
        if mandate["status"] != "active":
            raise conflict(f"Mandate is {mandate['status']}; filling a cart needs an active mandate.",
                           code="MANDATE_NOT_ACTIVE")
        quote = self.wallet.get_quote(actor, quote_id)
        store_id = quote["merchant_id"]
        if store_id not in mandate["policy"]["allowed_merchant_ids"]:
            raise conflict(f"The mandate does not allow {store_id}, so its cart is not touched.",
                           code="MERCHANT_NOT_ALLOWED")
        store = STORES.get(store_id)
        if store is None:
            raise invalid(f"{store_id} has no real-cart connection yet.")
        wanted: dict[int, dict] = {}
        for line in quote["items"]:
            sku = store.sku(line["product_id"])
            if sku is None:
                raise invalid(f"Product {line['product_id']} has no {store.name} SKU.")
            wanted[sku] = line

        result = {"store_id": store_id, "quote_id": quote_id, "mandate_id": mandate_id,
                  "observed_at": iso(self.wallet.clock.now()), "lines": [], "other_items": [],
                  "cart_subtotal_minor": None, "quote_subtotal_minor": quote["subtotal_minor"],
                  "checkout_ready": False}
        state = self.connections.session_state(actor.family_id, store_id)
        if state is None:
            return {**result, "status": "not_connected",
                    "message": f"Connect your {store.name} account first. Nothing was added."}

        with self._lock((actor.family_id, store_id)):
            try:
                with self.browser.session(store, state) as cart:
                    if not cart.signed_in():
                        self.connections.mark_expired(actor.family_id, store_id)
                        return {**result, "status": "session_expired",
                                "message": f"{store.name} signed you out. Sign in again; nothing was added."}
                    before = cart.cart()
                    if before.get("code") != "0000":
                        return {**result, "status": "store_error",
                                "message": f"{store.name} would not show the cart (code {before.get('code')}). "
                                           "Nothing was added."}
                    failure = self._write(cart, parse_cart(before), wanted)
                    after = parse_cart(cart.cart())
            except StoreBrowserError as exc:
                return {**result, "status": "store_error",
                        "message": f"{exc} Some items may have been added; open the cart to check."}
        return self._compare(result, store.name, wanted, after, failure)

    @staticmethod
    def _write(cart, current: dict[int, CartLine], wanted: dict[int, dict]) -> str | None:
        """Set each quoted line to its quantity. Stops at the first unanswered or refused write."""
        for sku, line in wanted.items():
            have = current[sku].quantity if sku in current else 0
            if have == line["quantity"]:
                continue
            try:
                answer = cart.add(sku, have, line["quantity"])
            except StoreBrowserError as exc:
                return str(exc)
            if answer.get("code") != "0000":
                return f"the shop refused {line['title']} (code {answer.get('code')})"
        return None

    @staticmethod
    def _compare(result: dict, store_name: str, wanted: dict[int, dict], cart: dict[int, CartLine],
                 failure: str | None) -> dict:
        lines, subtotal, all_ok = [], 0, True
        for sku, line in wanted.items():
            got = cart.get(sku)
            if got is None:
                status = "missing"
            elif got.quantity != line["quantity"]:
                status = "quantity_mismatch"
            elif got.unit_price_minor != line["unit_price_minor"]:
                status = "price_changed"
            else:
                status = "ok"
            all_ok &= status == "ok"
            if got is not None:
                subtotal += got.unit_price_minor * got.quantity
            lines.append({"product_id": line["product_id"], "sku": str(sku), "title": line["title"],
                          "quoted_quantity": line["quantity"], "cart_quantity": got.quantity if got else 0,
                          "quoted_unit_price_minor": line["unit_price_minor"],
                          "cart_unit_price_minor": got.unit_price_minor if got else None, "status": status})
        others = [{"sku": str(c.sku), "title": c.title, "quantity": c.quantity,
                   "unit_price_minor": c.unit_price_minor, "checked": c.checked}
                  for sku, c in cart.items() if sku not in wanted]
        extra = [o for o in others if o["checked"]]
        ready = all_ok and failure is None and not extra

        notes = []
        if failure:
            notes.append(f"Stopped early: {failure}.")
        changed = [l for l in lines if l["status"] == "price_changed"]
        if changed:
            notes.append(f"{store_name} now charges a different price for "
                         + ", ".join(l["title"] for l in changed) + ".")
        if any(l["status"] in ("missing", "quantity_mismatch") for l in lines):
            notes.append("Some quoted items are not in the cart as quoted.")
        if extra:
            notes.append(f"Your cart also has {len(extra)} other selected item(s) that checkout would include.")
        head = (f"Your {store_name} cart matches the quote." if ready
                else f"Items were added to your {store_name} cart, but it is not ready for checkout.")
        status = "synced" if ready else "partial" if failure else "mismatch"
        return {**result, "status": status, "lines": lines, "other_items": others,
                "cart_subtotal_minor": subtotal, "checkout_ready": ready,
                "message": " ".join([head, *notes, "Nothing was paid; Kumi never checks out."])}
