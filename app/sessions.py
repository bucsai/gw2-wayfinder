"""In-memory chat sessions. Nothing here is written to disk."""

import hashlib
import secrets
import time
from dataclasses import dataclass, field

from app.anonymize import Pseudonymizer
from app.gw2 import GW2Client

SESSION_TTL = 3600
RATE_LIMIT = 20  # messages per key
RATE_WINDOW = 600  # seconds


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Session:
    key_hash: str
    permissions: set[str]
    pseudo: Pseudonymizer
    guilds: dict[str, str]  # guild id -> real name
    history: list = field(default_factory=list)
    wiki_fetches: int = 0
    last_used: float = field(default_factory=time.time)


async def load_session(gw2: GW2Client, key: str) -> Session:
    """Validate the key and register all known names with a new pseudonymizer."""
    token = await gw2.get("/tokeninfo", key=key)
    permissions = set(token.get("permissions", []))
    pseudo = Pseudonymizer()
    account = await gw2.get("/account", key=key)
    pseudo.alias(account["name"], "account")
    if "characters" in permissions:
        for name in await gw2.get("/characters", key=key):
            pseudo.alias(name, "character")
    guilds = {}
    for guild_id in account.get("guilds", []):
        guild = await gw2.get(f"/guild/{guild_id}", key=key)
        guilds[guild_id] = guild["name"]
        pseudo.alias(guild["name"], "guild")
    return Session(key_hash(key), permissions, pseudo, guilds)


class SessionStore:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.message_times: dict[str, list[float]] = {}

    def add(self, session: Session) -> str:
        self._prune()
        session_id = secrets.token_urlsafe(24)
        self.sessions[session_id] = session
        return session_id

    def get(self, session_id: str, key: str) -> Session | None:
        session = self.sessions.get(session_id)
        if not session or session.key_hash != key_hash(key) or time.time() - session.last_used > SESSION_TTL:
            return None
        session.last_used = time.time()
        return session

    def allow_message(self, key: str) -> bool:
        now = time.time()
        times = [t for t in self.message_times.get(key_hash(key), []) if now - t < RATE_WINDOW]
        allowed = len(times) < RATE_LIMIT
        if allowed:
            times.append(now)
        self.message_times[key_hash(key)] = times
        return allowed

    def _prune(self) -> None:
        now = time.time()
        self.sessions = {k: s for k, s in self.sessions.items() if now - s.last_used <= SESSION_TTL}
        self.message_times = {k: t for k, t in self.message_times.items() if t and now - t[-1] < RATE_WINDOW}
