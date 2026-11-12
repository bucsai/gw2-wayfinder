import time

from app.cache import MISS, Cache


def test_get_set_and_expiry(monkeypatch):
    with Cache(":memory:") as cache:
        assert cache.get("a") is MISS
        cache.set("a", {"x": 1}, ttl=10)
        cache.set("b", None)
        assert cache.get("a") == {"x": 1}
        assert cache.get("b") is None

        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + 3600)
        assert cache.get("a") is MISS
        assert cache.get("b") is None


def test_persists_on_disk(tmp_path):
    path = tmp_path / "cache.db"
    with Cache(path) as cache:
        cache.set("k", [1, 2])
    with Cache(path) as cache:
        assert cache.get("k") == [1, 2]
