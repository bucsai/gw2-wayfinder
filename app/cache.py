"""SQLite key-value cache for public data. Never store account data here."""

import json
import sqlite3
import time
from pathlib import Path

MISS = object()


class Cache:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL)")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, key: str):
        """Return the cached value, or MISS. A cached None is a valid value."""
        row = self.db.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or (row[1] is not None and row[1] < time.time()):
            return MISS
        return json.loads(row[0])

    def set(self, key: str, value, ttl: float | None = None) -> None:
        """Store a value. ttl=None keeps it forever."""
        expires_at = time.time() + ttl if ttl is not None else None
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), expires_at),
            )
