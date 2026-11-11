import httpx
import pytest
import respx

from app.cache import Cache
from app.gw2 import BASE_URL, GW2Client, GW2Error


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield GW2Client(http, Cache(":memory:"))


@respx.mock
async def test_get_sends_key_and_maps_errors(client):
    route = respx.get(f"{BASE_URL}/account").respond(200, json={"age": 1})
    assert await client.get("/account", key="KEY") == {"age": 1}
    assert route.calls.last.request.headers["Authorization"] == "Bearer KEY"

    respx.get(f"{BASE_URL}/account/bank").respond(403, json={"text": "requires scope inventories"})
    with pytest.raises(GW2Error, match="permission"):
        await client.get("/account/bank", key="KEY")


@respx.mock
async def test_get_retries_once(client, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    route = respx.get(f"{BASE_URL}/build").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"id": 1})])
    assert await client.get("/build") == {"id": 1}
    assert route.call_count == 2


@respx.mock
async def test_get_many_batches_and_caches(client):
    route = respx.get(f"{BASE_URL}/items").respond(200, json=[{"id": 1, "name": "A"}])
    assert await client.get_many("/items", [1, 2, 1], ttl=60) == {1: {"id": 1, "name": "A"}}
    assert route.calls.last.request.url.params["ids"] == "1,2"

    assert await client.get_many("/items", [1, 2], ttl=60) == {1: {"id": 1, "name": "A"}}
    assert route.call_count == 1


async def _no_sleep(_):
    pass
