"""Client for the official GW2 Wiki (MediaWiki API).

The wiki is a community resource, so traffic is kept to a minimum:
results are cached forever (including misses), requests run one at a time
with a minimum interval, identical requests in flight are merged, and
throttling responses pause all requests.
"""

import asyncio
import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

import httpx

from app.cache import MISS, Cache
from app.config import USER_AGENT

API_URL = "https://wiki.guildwars2.com/api.php"
MIN_INTERVAL = 1.0
MAX_PAGE_CHARS = 8000


class WikiUnavailable(Exception):
    pass


class WikiClient:
    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.http = http
        self.cache = cache
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._paused_until = 0.0
        self._in_flight: dict[str, asyncio.Future] = {}

    async def search(self, query: str, on_fetch: Callable[[], None] | None = None) -> list[dict]:
        params = {"action": "query", "list": "search", "srsearch": query, "srlimit": "5", "srprop": "snippet"}
        return await self._cached(f"wiki:search:{query.strip().lower()}", params, _parse_search, on_fetch)

    async def page(self, title: str, on_fetch: Callable[[], None] | None = None) -> dict | None:
        """Return {title, ids, text} for a page, or None if it does not exist."""
        title = title.strip().replace("_", " ")
        title = title[:1].upper() + title[1:]
        params = {"action": "parse", "page": title, "prop": "text|wikitext", "redirects": "1"}
        return await self._cached(f"wiki:page:{title}", params, _parse_page, on_fetch)

    async def _cached(self, key: str, params: dict, parse: Callable, on_fetch: Callable[[], None] | None) -> Any:
        value = self.cache.get(key)
        if value is not MISS:
            return value
        if key in self._in_flight:
            return await asyncio.shield(self._in_flight[key])
        if on_fetch:
            on_fetch()
        future = asyncio.get_running_loop().create_future()
        self._in_flight[key] = future
        try:
            value = parse(await self._request(params))
            self.cache.set(key, value)
            future.set_result(value)
            return value
        except Exception as e:
            future.set_exception(e)
            future.exception()  # mark as retrieved when nobody else is waiting
            raise
        finally:
            del self._in_flight[key]

    async def _request(self, params: dict) -> dict:
        async with self._lock:
            now = time.monotonic()
            if now < self._paused_until:
                raise WikiUnavailable()
            await asyncio.sleep(max(0.0, self._last_request + MIN_INTERVAL - now))
            try:
                response = await self.http.get(
                    API_URL,
                    params={**params, "format": "json", "formatversion": "2", "maxlag": "5"},
                    headers={"User-Agent": USER_AGENT},
                )
            except httpx.HTTPError as e:
                raise WikiUnavailable() from e
            finally:
                self._last_request = time.monotonic()

            data = response.json() if response.status_code == 200 else {}
            error = data.get("error", {}).get("code")
            if response.status_code != 200 or (error and error != "missingtitle"):
                # Pause on any failure so a struggling wiki gets no further traffic.
                self._paused_until = time.monotonic() + _retry_after(response)
                raise WikiUnavailable()
            return data


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", 60)), 5), 600)
    except ValueError:
        return 60


def _parse_search(data: dict) -> list[dict]:
    results = data.get("query", {}).get("search", [])
    return [{"title": r["title"], "snippet": re.sub(r"<[^>]+>", "", r.get("snippet", ""))} for r in results]


def _parse_page(data: dict) -> dict | None:
    if "parse" not in data:
        return None  # missing page
    parse = data["parse"]
    ids = [int(i) for i in re.findall(r"^\|\s*id\s*=\s*(\d+)", parse.get("wikitext", ""), re.MULTILINE)]
    text = html_to_text(parse.get("text", ""))
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n[truncated]"
    return {"title": parse["title"], "ids": ids, "text": text}


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "table"}
    VOID = {"br", "img", "hr", "input", "meta", "link", "wbr"}
    SKIP_CLASSES = ("navbox", "mw-editsection", "reference", "toc")

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br" and not self.skip_depth:
                self.parts.append("\n")
            return
        classes = dict(attrs).get("class") or ""
        if self.skip_depth or tag in ("script", "style") or any(c in classes for c in self.SKIP_CLASSES):
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    text = "".join(extractor.parts)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
