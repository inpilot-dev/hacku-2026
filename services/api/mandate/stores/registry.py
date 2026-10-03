"""Stores whose real carts the agent can fill.

Wellcome and Market Place run on DFI's "superweb" shop platform. Its cart is a logged-in API
(`POST /api/cart/v20/cartInfo` and `/addToCart`, form fields `param` and
`comm`), and a guest add-to-cart redirects to yuu Rewards sign-in (mobile
number plus SMS code). Observed on 2026-10-03:

- `comm` is the page's own `localStorage.commData.comm` (vendor, store and
  shipment IDs for the account), so it is read from the session, never hard-coded.
- `addToCart` applies the difference `count - beforeCount` to a line, so the
  same request sent twice doubles it, and `count: 0` removes the line. Every
  write is therefore preceded by a cart read.
- Cart prices follow the account's fulfilment store and can differ from the
  public product page (a pear was HK$4.00 on the page and HK$4.50 in the cart).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit


@dataclass(frozen=True)
class SuperwebStore:
    id: str  # also the wallet merchant_id
    name: str
    origin: str
    domain: str  # cookie domain of the shop
    domain_flag: str  # the shop's `domain-flag` request header
    vender_id: int  # yuu Rewards `venderId` for the shop
    login_flag_cookie: str = "LOGIN_FLAG"

    def login_url(self) -> str:
        """yuu Rewards sign-in that returns to the shop's own login callback, then its cart page."""
        callback = f"{self.origin}/api/login/callback?target_url={quote(self.origin + '/en/cart', safe='')}"
        return (f"https://www.yuurewards.com/en/super/login-info?callbackUrl={callback}"
                f"&domain={self.domain}&venderId={self.vender_id}")

    def owns_url(self, url: str) -> bool:
        """True only for an https page on the shop's exact origin host (not a look-alike such as
        `www.wellcome.com.hk.evil.example`)."""
        page, own = urlsplit(url), urlsplit(self.origin)
        return page.scheme == "https" and page.hostname == own.hostname and page.port == own.port

    def owns_cookie(self, cookie_domain: str) -> bool:
        """True for a cookie set by the shop's domain or one of its subdomains (not `notwellcome.com.hk`)."""
        domain = cookie_domain.lstrip(".").lower()
        return domain == self.domain or domain.endswith("." + self.domain)

    def sku(self, product_id: str) -> int | None:
        """The shop's SKU behind a catalog product ID such as `wellcome_101355093`."""
        prefix = f"{self.id}_"
        if not product_id.startswith(prefix):
            return None
        tail = product_id[len(prefix):]
        return int(tail) if tail.isdigit() else None


STORES: dict[str, SuperwebStore] = {
    "wellcome": SuperwebStore("wellcome", "Wellcome", "https://www.wellcome.com.hk", "wellcome.com.hk",
                              "wellcome", 5),
    # Same platform and yuu sign-in; its own session, `domain-flag` and store IDs (seen in its
    # addToCart request on 2026-10-03: domain-flag "marketplace", comm.dmTenantId 0, storeId 188).
    "marketplace": SuperwebStore("marketplace", "Market Place", "https://www.marketplacehk.com", "marketplacehk.com",
                                 "marketplace", 5),
}
