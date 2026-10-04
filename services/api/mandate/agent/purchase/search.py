"""Web search through DuckDuckGo's HTML endpoint (no API key, plain HTTP)."""

from __future__ import annotations

import re
import os
import time
import threading
from datetime import datetime, timezone
from ..config import env_value
from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
RESULT = re.compile(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', re.S)
# Sites that compare or review rather than sell: their links lead to other shops.
NOT_SHOPS = re.compile(r"(^|\.)(biggo|price\.com|priceinto|mobilekishop|gsmarena|youtube|facebook|instagram|reddit|"
                       r"wikipedia|carousell|hi94|openrice|priceme|pricespy|idealo|pricerunner|amazon\.(?!com\.hk))", re.I)


class SearchError(Exception):
    pass


def duckduckgo_search(query: str, client: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    """[{url, title}] of organic results, ads and non-shop sites left out."""
    owns_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        response = client.post(SEARCH_URL, data={"q": query, "kl": "hk-tzh"}, headers={"User-Agent": UA})
    except httpx.HTTPError as exc:
        raise SearchError("Web search could not be reached. Please try again later.") from exc
    finally:
        if owns_client:
            client.close()
    if response.is_error:
        raise SearchError(f"Web search answered HTTP {response.status_code}.")
    if response.status_code == 202 or any(marker in response.text.lower() for marker in ("challenge-form", "anomaly.js", "anomaly-modal")):
        raise SearchError("The search provider requires a browser verification. Search is temporarily unavailable; please try again later.")
    results, seen = [], set()
    for href, title in RESULT.findall(response.text):
        url = unescape(parse_qs(urlparse(unescape(href)).query).get("uddg", [unescape(href)])[0])
        host = urlparse(url).hostname or ""
        if not url.startswith("http") or "duckduckgo.com" in host or NOT_SHOPS.search(host) or url in seen:
            continue
        seen.add(url)
        results.append({"url": url, "title": re.sub(r"<[^>]+>", "", unescape(title)).strip()})
        if len(results) >= limit:
            break
    return results


_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_CACHE_LOCK = threading.Lock()


def web_search(query: str, client: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    """Explicit provider selection. Failures never become invented product results.

    Set MANDATE_SEARCH_PROVIDER=serper and SERPER_API_KEY to use the supported
    search API. Default keeps the legacy provider; a challenge is a failure.
    Cached observations retain their original timestamp and expire after 120 s.
    """
    provider = os.environ.get("MANDATE_SEARCH_PROVIDER", "duckduckgo")
    if provider not in ("serper", "duckduckgo"):
        raise SearchError("Unsupported search provider; configure serper or duckduckgo.")
    cache_key = (provider, query, limit)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if client is None and cached and time.monotonic() - cached[0] < 120:
            return [dict(row) for row in cached[1]]
    if provider == "duckduckgo":
        result = duckduckgo_search(query, client, limit)
    else:
        key = env_value("SERPER_API_KEY")
        if not key:
            raise SearchError("Serper is selected but SERPER_API_KEY is not configured.")
        owns = client is None
        client = client or httpx.Client(timeout=12)
        try:
            for attempt in range(2):
                response = client.post("https://google.serper.dev/search", json={"q": query, "gl": "hk", "hl": "en", "num": min(limit, 20)}, headers={"X-API-KEY": key})
                if response.status_code in (429, 503) and attempt == 0:
                    time.sleep(0.3)
                    continue
                if response.is_error:
                    raise SearchError(f"Search provider answered HTTP {response.status_code}; no result was invented.")
                break
            data = response.json()
            result, seen = [], set()
            for item in data.get("organic", []):
                url = item.get("link", "")
                host = urlparse(url).hostname or ""
                if not url.startswith("https://") or NOT_SHOPS.search(host) or url in seen:
                    continue
                seen.add(url)
                result.append({"url": url, "title": str(item.get("title", ""))})
                if len(result) >= limit:
                    break
        except (httpx.HTTPError, ValueError, TypeError):
            raise SearchError("Search provider unavailable or response invalid.") from None
        finally:
            if owns:
                client.close()
    observed = datetime.now(timezone.utc).isoformat()
    result = [{**row, "provider": provider, "observed_at": observed} for row in result]
    with _CACHE_LOCK:
        # Bounded in-memory cache. Do not store an unlimited history of user searches.
        if len(_CACHE) >= 64:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[cache_key] = (time.monotonic(), result)
    return [dict(row) for row in result]
