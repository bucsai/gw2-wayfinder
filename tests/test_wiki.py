import asyncio

import httpx
import pytest
import respx

from app.cache import Cache
from app.config import USER_AGENT
from app.wiki import API_URL, WikiClient, WikiUnavailable, html_to_text

PAGE = {
    "parse": {
        "title": "Mystic Coin",
        "wikitext": "{{item infobox\n| rarity = rare\n| id = 19976\n}}\nText",
        "text": '<p>Used in the <a href="/x">Mystic Forge</a>.<span class="mw-editsection">[edit]</span></p>',
    }
}


@pytest.fixture
async def wiki(monkeypatch):
    monkeypatch.setattr("app.wiki.MIN_INTERVAL", 0)
    with Cache(":memory:") as cache:
        async with httpx.AsyncClient() as http:
            yield WikiClient(http, cache)


@respx.mock
async def test_page_is_parsed_and_cached(wiki):
    route = respx.get(API_URL).respond(200, json=PAGE)
    page = await wiki.page("mystic_Coin")
    assert page == {"title": "Mystic Coin", "ids": [19976], "text": "Used in the Mystic Forge."}
    assert await wiki.page("Mystic Coin") == page
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.url.params["maxlag"] == "5"


@respx.mock
async def test_missing_page_is_cached(wiki):
    route = respx.get(API_URL).respond(200, json={"error": {"code": "missingtitle"}})
    assert await wiki.page("Nope") is None
    assert await wiki.page("Nope") is None
    assert route.call_count == 1


@respx.mock
async def test_identical_requests_are_merged(wiki):
    route = respx.get(API_URL).respond(200, json={"query": {"search": [{"title": "A", "snippet": "<b>a</b>"}]}})
    results = await asyncio.gather(wiki.search("a"), wiki.search("A "))
    assert results == [[{"title": "A", "snippet": "a"}]] * 2
    assert route.call_count == 1


@respx.mock
async def test_throttling_pauses_without_retry(wiki):
    route = respx.get(API_URL).respond(429, headers={"Retry-After": "30"})
    with pytest.raises(WikiUnavailable):
        await wiki.search("a")
    with pytest.raises(WikiUnavailable):
        await wiki.search("b")
    assert route.call_count == 1


@pytest.mark.parametrize("code", ["maxlag", "internal_api_error_DBConnectionError"])
@respx.mock
async def test_api_errors_pause_and_are_not_cached(wiki, code):
    route = respx.get(API_URL).respond(200, json={"error": {"code": code}})
    with pytest.raises(WikiUnavailable):
        await wiki.page("a")
    with pytest.raises(WikiUnavailable):
        await wiki.page("b")
    assert route.call_count == 1

    wiki._paused_until = 0
    route.respond(200, json=PAGE)
    assert (await wiki.page("a"))["ids"] == [19976]


@respx.mock
async def test_on_fetch_only_called_for_network_requests(wiki):
    respx.get(API_URL).respond(200, json=PAGE)
    calls = []
    await wiki.page("Mystic Coin", on_fetch=lambda: calls.append(1))
    await wiki.page("Mystic Coin", on_fetch=lambda: calls.append(1))
    assert calls == [1]


def test_html_to_text_skips_navboxes():
    html = '<h2>Title</h2><table><tr><td>a</td><td>b</td></tr></table><div class="navbox"><p>nav</p></div>'
    assert html_to_text(html) == "Title\n\n| a | b"
