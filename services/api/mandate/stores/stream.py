"""Show the user the shop's sign-in page from the server's browser, and nothing else.

`WS /stores/{store_id}/login/stream` relays one tab: Chrome's screencast frames
go to the client as JPEG, and the client's pointer and keyboard input comes
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
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .registry import SuperwebStore
from .steel import CDP_URL, LoginWindow, StoreBrowserError

VIEWPORT = {"width": 412, "height": 780}
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
        try:
            session = (await cdp.call("Target.attachToTarget", targetId=window.target_id, flatten=True))["sessionId"]
            await cdp.call("Page.enable", session)

            async def phone_viewport() -> None:
                await cdp.call("Emulation.setDeviceMetricsOverride", session, width=VIEWPORT["width"],
                               height=VIEWPORT["height"], deviceScaleFactor=2, mobile=True)

            await phone_viewport()
            await cdp.call("Page.startScreencast", session, format="jpeg", quality=70,
                           maxWidth=VIEWPORT["width"] * 2, maxHeight=VIEWPORT["height"] * 2)
            await send({"type": "viewport", **VIEWPORT})

            async def frames() -> None:
                while (event := await cdp.events.get()) is not None:
                    if event.get("sessionId") != session:
                        continue
                    method, params = event.get("method"), event.get("params", {})
                    if method == "Page.screencastFrame":
                        meta = params.get("metadata", {})
                        await send({"type": "frame", "data": params["data"],
                                    "width": round(meta.get("deviceWidth") or VIEWPORT["width"]),
                                    "height": round(meta.get("deviceHeight") or VIEWPORT["height"])})
                        await cdp.call("Page.screencastFrameAck", session, sessionId=params["sessionId"])
                    elif method == "Page.frameNavigated" and not params["frame"].get("parentId"):
                        # A navigation that swaps renderer drops the override, so set it again.
                        await phone_viewport()
                        if not allowed_url(params["frame"].get("url", ""), store):
                            await send({"type": "notice", "text": f"Only the {store.name} sign-in can be used here."})
                            await cdp.call("Page.navigate", session, url=store.login_url())

            async def inputs() -> None:
                while (msg := await receive()) is not None:  # None: the client left
                    for method, params in input_commands(msg):
                        await cdp.call(method, session, **params)

            async def watch() -> None:
                while (status := await finished()) is None:
                    await asyncio.sleep(poll_s)
                await send({"type": "status", "status": status})

            tasks = [asyncio.create_task(t()) for t in (frames, inputs, watch)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() and not isinstance(task.exception(), (StoreBrowserError, ConnectionError)):
                        raise task.exception()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            reader.cancel()
