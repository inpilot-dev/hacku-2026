"""Jev (browser-use/jev-ultrafast) driving a Steel browser over CDP.

Jev only navigates: it picks which control to click toward a goal. Reading
prices is done by product_data on the pages it reaches. Requires a running
Steel browser (``just steel``) and TYPESAFE_API_KEY plus an OpenRouter key in
the repository's .env.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_env(path: Path = REPO_ROOT / ".env") -> None:
    """Load KEY=value lines without overriding the environment; map OPENROUTER_KEY for jev's text helper."""
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if os.environ.get("OPENROUTER_KEY"):
        os.environ.setdefault("TEXT_MODEL_API_KEY", os.environ["OPENROUTER_KEY"])
    os.environ.setdefault("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    os.environ.setdefault("TEXT_MODEL", "inception/mercury-2.5")
    os.environ.setdefault("TEXT_MODEL_REASONING", "none")
    os.environ.setdefault("BU_CDP_WS", "ws://localhost:3000/")  # self-hosted Steel
    os.environ.setdefault("BH_TELEMETRY", "0")
    missing = [k for k in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)} (set them in the repository .env).")


_patched = False


def _patch_jev() -> None:
    """Two fixes for jev-ultrafast@1231850, applied once.

    - CDP calls get 30 s instead of browser_harness's 5 s default, which heavy
      pages exceed during Page.navigate.
    - The first observation retries while a page is still redirecting (StalePage).
    """
    global _patched
    if _patched:
        return
    from browser_harness.helpers import cdp
    from jev_ultrafast import browser as jb

    def call(self, method, **params):
        return cdp(method, session_id=self.session, _response_timeout=30, **params)

    original_observe = jb.Browser.observe

    def observe(self, *args, **kwargs):
        for _ in range(15):
            try:
                return original_observe(self, *args, **kwargs)
            except jb.StalePage:
                time.sleep(1)
        return original_observe(self, *args, **kwargs)

    jb.Browser.call = call
    jb.Browser.observe = observe
    _patched = True


LINKS_JS = """JSON.stringify([...document.querySelectorAll('a[href]')].map(a => ({href: a.href,
  text: (a.innerText || a.title || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim()})))"""
PAGE_JS = """JSON.stringify({url: location.href, html: document.documentElement.outerHTML,
  text: document.body ? document.body.innerText : '',
  ld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent)})"""


class JevSession:
    """One browser tab: Jev navigates toward a goal, then pages are read in the same tab."""

    def __init__(self, start_url: str, goal: str):
        load_env()
        _patch_jev()
        from browser_harness.helpers import cdp
        from jev_ultrafast import Agent

        self.agent = Agent(start_url, [goal])
        # Make the tab visible in the Steel viewer (http://localhost:3000/ui).
        cdp("Target.activateTarget", targetId=self.agent.browser.target)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.agent.close()

    def run(self) -> str:
        """Let Jev act until it reports done or blocked; returns that status."""
        state = None
        for state in self.agent.run():
            pass
        return state["status"] if state else "blocked"

    def snapshot(self) -> dict:
        return json.loads(self.agent.browser.evaluate(PAGE_JS))

    def links(self) -> list[dict]:
        return json.loads(self.agent.browser.evaluate(LINKS_JS))

    def open(self, url: str, timeout_s: float = 20) -> dict:
        self.agent.browser.call("Page.navigate", url=url)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.agent.browser.evaluate("document.readyState") == "complete":
                    break
            except Exception:  # the document is swapped mid-navigation
                pass
            time.sleep(0.25)
        return self.snapshot()
