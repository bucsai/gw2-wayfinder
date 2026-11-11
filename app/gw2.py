"""Client for the official GW2 API (https://wiki.guildwars2.com/wiki/API:Main)."""

import asyncio

import httpx

from app.cache import MISS, Cache

BASE_URL = "https://api.guildwars2.com/v2"
SCHEMA_VERSION = "2024-07-20T01:00:00.000Z"
BATCH_SIZE = 200


class GW2Error(Exception):
    pass


class GW2Client:
    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.http = http
        self.cache = cache

    async def get(self, path: str, key: str | None = None, params: dict | None = None):
        headers = {"X-Schema-Version": SCHEMA_VERSION}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        response = await self._send(path, params, headers)
        if response.status_code == 429 or response.status_code >= 500:
            await asyncio.sleep(1)
            response = await self._send(path, params, headers)
        if response.status_code in (401, 403):
            raise GW2Error("The API key is invalid or lacks the required permission.")
        if response.status_code == 404:
            raise GW2Error("Not found.")
        if response.status_code >= 400:
            raise GW2Error(f"GW2 API returned HTTP {response.status_code}.")
        return response.json()

    async def _send(self, path: str, params: dict | None, headers: dict) -> httpx.Response:
        try:
            return await self.http.get(f"{BASE_URL}{path}", params=params, headers=headers)
        except httpx.HTTPError as e:
            raise GW2Error(f"GW2 API request failed: {type(e).__name__}") from e

    async def get_cached(self, path: str, ttl: float, params: dict | None = None):
        """GET a public endpoint through the cache."""
        cache_key = f"gw2:{path}?{sorted((params or {}).items())}"
        value = self.cache.get(cache_key)
        if value is MISS:
            value = await self.get(path, params=params)
            self.cache.set(cache_key, value, ttl)
        return value

    async def get_many(self, path: str, ids, ttl: float) -> dict[int, dict]:
        """Fetch public objects by id, batched and cached per id. Unknown ids are left out."""
        result, missing = {}, []
        for id_ in dict.fromkeys(ids):
            value = self.cache.get(f"gw2:{path}/{id_}")
            if value is MISS:
                missing.append(id_)
            elif value is not None:
                result[id_] = value
        for i in range(0, len(missing), BATCH_SIZE):
            batch = missing[i : i + BATCH_SIZE]
            try:
                objects = await self.get(path, params={"ids": ",".join(map(str, batch))})
            except GW2Error as e:
                if str(e) != "Not found.":  # all ids in the batch are unknown
                    raise
                objects = []
            found = {obj["id"]: obj for obj in objects}
            for id_ in batch:
                self.cache.set(f"gw2:{path}/{id_}", found.get(id_), ttl)
            result.update(found)
        return result
