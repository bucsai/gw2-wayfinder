import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ContentFilterError, UsageLimitExceeded

from app import agent, config
from app.cache import Cache
from app.gw2 import GW2Client, GW2Error
from app.sessions import SessionStore, load_session, transcript
from app.tools import Deps
from app.wiki import WikiClient

log = logging.getLogger("wayfinder")
STATIC_DIR = Path(__file__).parent.parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    with Cache(config.DATA_DIR / "cache.db") as cache:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": config.USER_AGENT}) as http:
            app.state.gw2 = GW2Client(http, cache)
            app.state.wiki = WikiClient(http, cache)
            app.state.sessions = SessionStore()
            yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


class SessionRequest(BaseModel):
    session_id: str


class ChatRequest(SessionRequest):
    message: str = Field(min_length=1, max_length=2000)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/session")
async def create_session(request: Request, x_gw2_key: str = Header()):
    key = x_gw2_key.strip()
    # Checked before the key is validated: starting a session makes several GW2 API calls.
    if not request.app.state.sessions.allow("session", key):
        raise HTTPException(429, "Too many new chats. Try again in a few minutes.")
    try:
        session = await load_session(request.app.state.gw2, key)
    except GW2Error:
        raise HTTPException(401, "The API key is invalid or lacks the account permission.") from None
    session_id = request.app.state.sessions.add(session)
    return {"session_id": session_id, "permissions": sorted(session.permissions)}


@app.post("/api/history")
async def history(request: Request, body: SessionRequest, x_gw2_key: str = Header()):
    session = request.app.state.sessions.get(body.session_id, x_gw2_key.strip())
    if session is None:
        raise HTTPException(401, "Session expired.")
    return {"messages": transcript(session)}


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest, x_gw2_key: str = Header()):
    state = request.app.state
    key = x_gw2_key.strip()
    session = state.sessions.get(body.session_id, key)
    if session is None:
        raise HTTPException(401, "Session expired.")
    sessions = state.sessions
    if sessions.running(key):
        raise HTTPException(409, "Wait for the current answer to finish.")
    if sessions.server_busy():
        raise HTTPException(503, "Wayfinder is busy right now. Try again in a minute.")
    if not sessions.allow("message", key):
        raise HTTPException(429, "Too many messages. Try again in a few minutes.")
    # Taken here, not in the stream, so a second request cannot slip in before the stream starts.
    sessions.start_run(key)
    deps = Deps(state.gw2, state.wiki, key, session)
    return StreamingResponse(_stream(deps, body.message, sessions), media_type="application/x-ndjson")


async def _stream(deps: Deps, message: str, sessions: SessionStore):
    """Stream NDJSON events. Text is sent per line so aliases can be restored to real names."""
    session = deps.session
    buffer = ""
    try:
        async for kind, value in agent.run(deps, message):
            if kind == "tool":
                yield _event(tool=value)
                continue
            buffer += value
            lines, newline, buffer = buffer.rpartition("\n")
            if newline:
                yield _event(text=session.pseudo.restore(lines + newline))
        if buffer:
            yield _event(text=session.pseudo.restore(buffer))
    except ContentFilterError:
        yield _event(error="Sorry, I can't help with that request.")
    except UsageLimitExceeded:
        yield _event(error="That question needed too many steps. Try a narrower question.")
    except Exception as e:
        # Log the type only. Messages can contain player data.
        log.error("Chat failed: %s", type(e).__name__)
        yield _event(error="Something went wrong. Please try again.")
    finally:
        sessions.end_run(deps.key)


def _event(**data) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
