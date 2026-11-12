"""In-memory chat sessions. Nothing here is written to disk."""

import hashlib
import secrets
import time
from dataclasses import dataclass, field

from pydantic_ai.messages import TextPart, UserPromptPart

from app.anonymize import Pseudonymizer
from app.gw2 import GW2Client

SESSION_TTL = 3600
RATE_WINDOW = 600  # seconds
RATE_LIMITS = {"message": 20, "session": 5}  # per key per RATE_WINDOW
MAX_RUNS = 5  # answers running at once, across all keys
RUN_TIMEOUT = 300  # seconds; a run lock older than this is ignored in case it was never released


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


def transcript(session: Session) -> list[dict[str, str]]:
    """The chat so far as user and assistant turns, with real names restored. Tool calls are left out."""
    turns: list[dict[str, str]] = []
    for message in session.history:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                turns.append({"role": "user", "text": part.content})
            elif isinstance(part, TextPart) and part.content:
                # Text parts of one answer are joined the same way they are streamed.
                if turns and turns[-1]["role"] == "assistant":
                    turns[-1]["text"] += "\n\n" + part.content
                else:
                    turns.append({"role": "assistant", "text": part.content})
    return [{"role": turn["role"], "text": session.pseudo.restore(turn["text"])} for turn in turns]


class SessionStore:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.request_times: dict[tuple[str, str], list[float]] = {}  # (kind, key hash) -> times
        self.runs: dict[str, float] = {}  # key hash -> start time of its running answer

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

    def allow(self, kind: str, key: str) -> bool:
        """Count a request of this kind ("message" or "session") for the key, unless the key is over its limit."""
        now = time.time()
        slot = (kind, key_hash(key))
        times = [t for t in self.request_times.get(slot, []) if now - t < RATE_WINDOW]
        allowed = len(times) < RATE_LIMITS[kind]
        if allowed:
            times.append(now)
        self.request_times[slot] = times
        return allowed

    def running(self, key: str) -> bool:
        started = self.runs.get(key_hash(key))
        return started is not None and time.time() - started < RUN_TIMEOUT

    def server_busy(self) -> bool:
        now = time.time()
        self.runs = {k: t for k, t in self.runs.items() if now - t < RUN_TIMEOUT}
        return len(self.runs) >= MAX_RUNS

    def start_run(self, key: str) -> None:
        self.runs[key_hash(key)] = time.time()

    def end_run(self, key: str) -> None:
        self.runs.pop(key_hash(key), None)

    def _prune(self) -> None:
        now = time.time()
        self.sessions = {k: s for k, s in self.sessions.items() if now - s.last_used <= SESSION_TTL}
        self.request_times = {k: t for k, t in self.request_times.items() if t and now - t[-1] < RATE_WINDOW}
