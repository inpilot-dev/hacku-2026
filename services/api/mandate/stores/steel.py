"""The shop's own pages in a self-hosted Steel browser, over the Chrome DevTools Protocol.

Two jobs, each in its own isolated browser context (separate cookie jar):

- **Sign-in.** The shop's yuu Rewards page opens in a fresh context, and that
  one tab is relayed to the user's screen (stream.py), where they type their
  mobile number and SMS code themselves. Once the shop's login cookie appears, its cookies and
  localStorage are returned for the server to keep. The user's code never
  passes through this service.
- **Cart calls.** A saved session is restored into a fresh, throw-away context,
  and the shop's own cart API is called from its page with `fetch`, exactly as
  the shop's site does. No model runs in these contexts.

Steel is one browser for the whole machine (Jev's catalog capture uses it too);
contexts keep each user's cookies apart. A shared deployment should still give
each user their own short-lived browser.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from websockets.sync.client import connect

from .registry import SuperwebStore

CDP_URL = os.environ.get("MANDATE_STEEL_CDP_URL", "ws://127.0.0.1:3000/")
COOKIE_FIELDS = ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires")

CALL_JS = """(async (path, param, flag) => {
  const saved = JSON.parse(localStorage.getItem('commData') || '{}');
  const comm = saved.comm || saved;
  if (param.storeGroup) param.storeGroup.forEach(g => { g.erpStoreId = comm.storeId; });
  const body = new URLSearchParams({param: JSON.stringify(param), comm: JSON.stringify(comm)});
  const r = await fetch(path, {method: 'POST', credentials: 'include', redirect: 'manual', body,
    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'domain-flag': flag}});
  return JSON.stringify({status: r.status, text: r.type === 'opaqueredirect' ? '' : await r.text()});
})"""


class StoreBrowserError(Exception):
    """The browser or the shop could not be reached; nothing is known to have changed."""


class _Cdp:
    """Minimal flat-session CDP client: one websocket, calls answered in order."""

    def __init__(self, url: str = CDP_URL, timeout_s: float = 30):
        try:
            self.ws = connect(url, max_size=2**26, open_timeout=10)
        except OSError as exc:
            raise StoreBrowserError(f"Steel browser is not reachable at {url} (run `just steel`).") from exc
        self.timeout_s = timeout_s
        self.ids = itertools.count(1)

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, session: str | None = None, **params):
        msg_id = next(self.ids)
        msg = {"id": msg_id, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StoreBrowserError(f"Browser did not answer {method} in time.")
            reply = json.loads(self.ws.recv(timeout=remaining))
            if reply.get("id") == msg_id:
                if "error" in reply:
                    raise StoreBrowserError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})

    def attach(self, target_id: str) -> str:
        return self.call("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]

    def evaluate(self, session: str, expression: str):
        result = self.call("Runtime.evaluate", session, expression=expression, awaitPromise=True, returnByValue=True)
        if result.get("exceptionDetails"):
            raise StoreBrowserError("Script failed on the shop page.")
        return result["result"].get("value")

    def wait_loaded(self, session: str, timeout_s: float = 30) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.evaluate(session, "document.readyState") == "complete":
                    return
            except StoreBrowserError:
                pass  # the document is swapped mid-navigation
            time.sleep(0.5)
        raise StoreBrowserError("The shop page did not finish loading.")


@dataclass(frozen=True)
class LoginWindow:
    context_id: str
    target_id: str


class SteelCart:
    """The shop's cart API, called from a page whose session was restored."""

    def __init__(self, cdp: _Cdp, session: str, context_id: str, store: SuperwebStore):
        self.cdp, self.session, self.context_id, self.store = cdp, session, context_id, store

    def signed_in(self) -> bool:
        cookies = self.cdp.call("Storage.getCookies", browserContextId=self.context_id)["cookies"]
        url = self.cdp.evaluate(self.session, "location.href") or ""
        return url.startswith(self.store.origin) and any(
            c["name"] == self.store.login_flag_cookie and c["domain"].lstrip(".").endswith(self.store.domain)
            for c in cookies)

    def _post(self, path: str, param: dict) -> dict:
        args = ", ".join(json.dumps(a) for a in (path, param, self.store.domain_flag))
        raw = json.loads(self.cdp.evaluate(self.session, f"({CALL_JS})({args})"))
        if raw["status"] != 200:
            raise StoreBrowserError(f"{self.store.name} answered HTTP {raw['status'] or 'redirect'} for {path}.")
        try:
            return json.loads(raw["text"])
        except json.JSONDecodeError as exc:
            raise StoreBrowserError(f"{self.store.name} sent a non-JSON answer for {path}.") from exc

    def cart(self) -> dict:
        return self._post("/api/cart/v20/cartInfo", {"isCollage": False, "simple": 0, "sort": 1})

    def add(self, sku: int, before: int, count: int) -> dict:
        """Change one line from `before` to `count` (the shop applies the difference)."""
        return self._post("/api/cart/v20/addToCart", {
            "isCollage": False, "sort": 1, "simple": 0, "source": 2,
            "storeGroup": [{"erpStoreId": 0, "wares": [{"sku": sku, "checked": 1, "beforeCount": before,
                                                         "count": count}]}]})


class SteelStoreBrowser:
    def __init__(self, cdp_url: str = CDP_URL):
        self.cdp_url = cdp_url

    # ---------------------------------------------------------------- sign-in

    def begin_login(self, store: SuperwebStore) -> LoginWindow:
        cdp = _Cdp(self.cdp_url)
        try:
            context = cdp.call("Target.createBrowserContext", disposeOnDetach=False)["browserContextId"]
            target = cdp.call("Target.createTarget", url=store.login_url(), browserContextId=context)["targetId"]
            return LoginWindow(context, target)
        finally:
            cdp.close()

    def poll_login(self, window: LoginWindow, store: SuperwebStore) -> dict | None:
        """The saved session once the shop's login cookie is set and its page is back; else None."""
        cdp = _Cdp(self.cdp_url)
        try:
            cookies = cdp.call("Storage.getCookies", browserContextId=window.context_id)["cookies"]
            mine = [{k: c[k] for k in COOKIE_FIELDS if k in c} for c in cookies
                    if c["domain"].lstrip(".").endswith(store.domain)]
            if not any(c["name"] == store.login_flag_cookie for c in mine):
                return None
            session = cdp.attach(window.target_id)
            if not (cdp.evaluate(session, "location.href") or "").startswith(store.origin):
                return None
            local = json.loads(cdp.evaluate(session, "JSON.stringify(Object.fromEntries(Object.entries(localStorage)))"))
            if "commData" not in local:
                return None  # the shop has not finished setting up the signed-in page yet
            return {"cookies": mine, "local_storage": local}
        finally:
            cdp.close()

    def end_login(self, window: LoginWindow) -> None:
        cdp = _Cdp(self.cdp_url)
        try:
            try:
                cdp.call("Target.closeTarget", targetId=window.target_id)
            except StoreBrowserError:
                pass  # the user may have closed it already
            cdp.call("Target.disposeBrowserContext", browserContextId=window.context_id)
        except StoreBrowserError:
            pass
        finally:
            cdp.close()

    # ------------------------------------------------------------------ carts

    @contextmanager
    def session(self, store: SuperwebStore, state: dict) -> Iterator[SteelCart]:
        cdp = _Cdp(self.cdp_url)
        context = None
        try:
            context = cdp.call("Target.createBrowserContext", disposeOnDetach=True)["browserContextId"]
            cdp.call("Storage.setCookies", browserContextId=context, cookies=state["cookies"])
            target = cdp.call("Target.createTarget", url="about:blank", browserContextId=context)["targetId"]
            session = cdp.attach(target)
            restore = (f"if (location.origin === {json.dumps(store.origin)} && !sessionStorage.__mandateRestored) "
                       "{ sessionStorage.__mandateRestored = 1; "
                       f"for (const [k, v] of Object.entries({json.dumps(state['local_storage'])})) "
                       "localStorage.setItem(k, v); }")
            cdp.call("Page.enable", session)
            cdp.call("Page.addScriptToEvaluateOnNewDocument", session, source=restore)
            cdp.call("Page.navigate", session, url=f"{store.origin}/en")
            cdp.wait_loaded(session)
            yield SteelCart(cdp, session, context, store)
        finally:
            if context is not None:
                try:
                    cdp.call("Target.disposeBrowserContext", browserContextId=context)
                except StoreBrowserError:
                    pass
            cdp.close()
