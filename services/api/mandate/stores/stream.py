"""Show the user the shop's sign-in page from the server's browser, and nothing else.

`WS /stores/{store_id}/login/stream` relays one tab: screenshots of it go to
the client as JPEG (about four a second, only when the page changed), and the client's pointer and keyboard input comes
back as CDP `Input` events for that tab only. The relay:

- streams only the sign-in tab of that user's pending login (other tabs and
  other users' contexts are never attached);
- turns client messages into a small, validated set of input events
  (`input_commands`), so a client cannot send arbitrary CDP commands;
- keeps the tab on yuu Rewards and the shop's own site: any other main-frame
  navigation is sent back to the sign-in page, so the server's browser is not
  an open proxy;
- renders the page at a phone-sized viewport so it fits a phone screen too.

The user types their mobile number and SMS code into the shop's own page. The
keystrokes pass through this relay and are not stored or logged.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .registry import SuperwebStore
from .steel import CDP_URL, LoginWindow, StoreBrowserError

VIEWPORT = {"width": 412, "height": 860}  # until the client says how much room it has
VIEWPORT_LIMITS = {"width": (320, 480), "height": (420, 1000)}  # phone-width layouts only


def resized(msg: object) -> dict | None:
    """The page size a `resize` message asks for, clamped to phone-like bounds; None for anything else."""
    if not isinstance(msg, dict) or msg.get("type") != "resize":
        return None
    size = {}
    for key, (low, high) in VIEWPORT_LIMITS.items():
        value = _num(msg.get(key), low, high)
        if value is None:
            return None
        size[key] = int(value)
    return size
log = logging.getLogger(__name__)

FRAME_INTERVAL_S = 0.25
INPUT_SETTLE_S = 0.06  # after a tap or key, capture this soon instead of waiting for the next frame
TAB_GONE_S = 8  # captures failing this long mean the tab is gone
MAX_COORD = 4096  # frames report their own size; this only bounds nonsense input
KEYS = {"Backspace": 8, "Tab": 9, "Enter": 13, "Escape": 27, "ArrowLeft": 37, "ArrowRight": 39, "Delete": 46}
YUU_HOSTS = ("yuurewards.com",)
MAX_TEXT = 64


def allowed_url(url: str, store: SuperwebStore) -> bool:
    parts = urlsplit(url)
    if parts.scheme in ("about", "data", "blob"):
        return True  # blank and inline frames the sign-in page creates itself
    if parts.scheme != "https" or not parts.hostname:
        return False
    host = parts.hostname
    return any(host == h or host.endswith("." + h) for h in (*YUU_HOSTS, store.domain))


def _num(value, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(low, min(high, float(value)))


def input_commands(msg: object) -> list[tuple[str, dict]]:
    """CDP Input commands for one client message; [] for anything not understood."""
    if not isinstance(msg, dict):
        return []
    kind = msg.get("type")
    if kind in ("down", "up", "move"):
        x, y = _num(msg.get("x"), 0, MAX_COORD), _num(msg.get("y"), 0, MAX_COORD)
        if x is None or y is None:
            return []
        event = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}[kind]
        params = {"type": event, "x": x, "y": y, "button": "left" if kind != "move" else "none",
                  "buttons": 1 if kind == "down" else 0}
        if kind != "move":
            params["clickCount"] = 1
        if kind == "down":  # move the pointer there first, as a real mouse would
            return [("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0}),
                    ("Input.dispatchMouseEvent", params)]
        return [("Input.dispatchMouseEvent", params)]
    if kind == "wheel":
        x, y = _num(msg.get("x"), 0, MAX_COORD), _num(msg.get("y"), 0, MAX_COORD)
        dx, dy = _num(msg.get("dx", 0), -2000, 2000), _num(msg.get("dy", 0), -2000, 2000)
        if None in (x, y, dx, dy):
            return []
        return [("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy})]
    if kind == "text":
        text = msg.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT or not text.isprintable():
            return []
        return [("Input.insertText", {"text": text})]
    if kind == "key":
        key = msg.get("key")
        if key not in KEYS:
            return []
        base = {"key": key, "code": key, "windowsVirtualKeyCode": KEYS[key], "nativeVirtualKeyCode": KEYS[key]}
        down = {"type": "rawKeyDown" if key != "Enter" else "keyDown", **base}
        if key == "Enter":
            down["text"] = "\r"
        return [("Input.dispatchKeyEvent", down), ("Input.dispatchKeyEvent", {"type": "keyUp", **base})]
    return []


class _AsyncCdp:
    def __init__(self, ws):
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()

    async def read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            future = self.pending.pop(msg.get("id"), None) if "id" in msg else None
            if future is not None:
                if not future.done():
                    future.set_result(msg)
            else:
                await self.events.put(msg)
        await self.events.put(None)  # browser connection closed

    async def call(self, method: str, session: str | None = None, **params) -> dict:
        msg_id = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = future
        msg = {"id": msg_id, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        await self.ws.send(json.dumps(msg))
        reply = await asyncio.wait_for(future, 30)
        if "error" in reply:
            raise StoreBrowserError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})


async def relay_login(window: LoginWindow, store: SuperwebStore,
                      send: Callable[[dict], Awaitable[None]],
                      receive: Callable[[], Awaitable[object | None]],
                      finished: Callable[[], Awaitable[str | None]],
                      cdp_url: str = CDP_URL, poll_s: float = 2.0) -> None:
    """Relay the sign-in tab until `finished()` returns a final status, the client leaves or the tab closes."""
    async with connect(cdp_url, max_size=2**26) as ws:
        cdp = _AsyncCdp(ws)
        reader = asyncio.create_task(cdp.read())
        viewport = dict(VIEWPORT)
        try:
            session = (await cdp.call("Target.attachToTarget", targetId=window.target_id, flatten=True))["sessionId"]
            await cdp.call("Page.enable", session)

            async def phone_viewport() -> None:
                # A narrow desktop viewport: the site's responsive CSS gives the phone layout. Mobile
                # emulation (`mobile: True`) set during a page load left yuu's sign-in stuck loading.
                await cdp.call("Emulation.setDeviceMetricsOverride", session, width=viewport["width"],
                               height=viewport["height"], deviceScaleFactor=2, mobile=False)

            await phone_viewport()
            # A background tab is throttled (timers, painting), which left yuu's splash screen up forever.
            await cdp.call("Page.bringToFront", session)
            await cdp.call("Emulation.setFocusEmulationEnabled", session, enabled=True)
            await send({"type": "viewport", **viewport})

            wake = asyncio.Event()  # set after input so the result is captured at once

            async def frames() -> None:
                # Polled screenshots, not Page.startScreencast: in Steel's browser the screencast stopped
                # sending frames after the first paint, while a capture always paints the current page.
                # A capture can fail while the page is between documents (yuu's steps, the redirect back to
                # the store); that is skipped, and only a tab that stays unreachable ends the stream.
                previous, failures = None, 0
                while True:
                    try:
                        shot = await cdp.call("Page.captureScreenshot", session, format="jpeg", quality=65,
                                              optimizeForSpeed=True)
                        failures = 0
                    except StoreBrowserError:
                        failures += 1
                        if failures * FRAME_INTERVAL_S > TAB_GONE_S:
                            raise
                        shot = None
                    if shot and shot["data"] != previous:
                        previous = shot["data"]
                        await send({"type": "frame", "data": shot["data"], **viewport})
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), FRAME_INTERVAL_S)
                        await asyncio.sleep(INPUT_SETTLE_S)  # let the page react before capturing
                    except asyncio.TimeoutError:
                        pass

            async def navigation() -> None:
                while (event := await cdp.events.get()) is not None:
                    if event.get("sessionId") != session:
                        continue
                    if event.get("method") in ("Page.domContentEventFired", "Page.loadEventFired"):
                        # A size set while the first document was still loading can be lost when the
                        # renderer swaps, and re-sending the same size is then ignored: clear, then set.
                        try:
                            await cdp.call("Emulation.clearDeviceMetricsOverride", session)
                            await phone_viewport()
                        except StoreBrowserError:
                            pass
                        continue
                    if event.get("method") != "Page.frameNavigated":
                        continue
                    frame = event["params"]["frame"]
                    if frame.get("parentId"):
                        continue
                    try:
                        # A navigation that swaps renderer drops the override, so set it again.
                        await phone_viewport()
                        if not allowed_url(frame.get("url", ""), store):
                            await send({"type": "notice", "text": f"Only the {store.name} sign-in can be used here."})
                            await cdp.call("Page.navigate", session, url=store.login_url())
                    except StoreBrowserError:
                        log.info("sign-in relay: navigation step failed for %s", store.id)

            async def inputs() -> None:
                while (msg := await receive()) is not None:  # None: the client left
                    if (size := resized(msg)) is not None:
                        viewport.update(size)  # the client's room for the page, so it shows 1:1, uncut
                        try:
                            await phone_viewport()
                        except StoreBrowserError:
                            log.info("sign-in relay: resize failed for %s", store.id)
                        await send({"type": "viewport", **viewport})
                    for method, params in input_commands(msg):
                        try:
                            await cdp.call(method, session, **params)
                        except StoreBrowserError:
                            log.info("sign-in relay: input dropped for %s", store.id)  # page between documents
                    wake.set()

            async def watch() -> None:
                while (status := await finished()) is None:
                    await asyncio.sleep(poll_s)
                await send({"type": "status", "status": status})

            tasks = [asyncio.create_task(t()) for t in (frames, navigation, inputs, watch)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    exc = task.exception()
                    if exc is None:
                        continue
                    if not isinstance(exc, (StoreBrowserError, ConnectionError)):
                        raise exc
                    log.warning("sign-in relay for %s ended: %s", store.id, exc)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            reader.cancel()
