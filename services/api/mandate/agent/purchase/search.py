"""Web search through DuckDuckGo's HTML endpoint (no API key, plain HTTP)."""

from __future__ import annotations

import re
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


def web_search(query: str, client: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    """[{url, title}] of organic results, ads and non-shop sites left out."""
    client = client or httpx.Client(timeout=20)
    try:
        response = client.post(SEARCH_URL, data={"q": query, "kl": "hk-tzh"}, headers={"User-Agent": UA})
    except httpx.HTTPError as exc:
        raise SearchError("Web search could not be reached.") from exc
    if response.is_error:
        raise SearchError(f"Web search answered HTTP {response.status_code}.")
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
