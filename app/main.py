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
from app.sessions import SessionStore, load_session
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
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": config.USER_AGENT}) as http:
        cache = Cache(config.DATA_DIR / "cache.db")
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


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=2000)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/session")
async def create_session(request: Request, x_gw2_key: str = Header()):
    try:
        session = await load_session(request.app.state.gw2, x_gw2_key.strip())
    except GW2Error:
        raise HTTPException(401, "The API key is invalid or lacks the account permission.")
    session_id = request.app.state.sessions.add(session)
    return {"session_id": session_id, "permissions": sorted(session.permissions)}


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest, x_gw2_key: str = Header()):
    state = request.app.state
    key = x_gw2_key.strip()
    session = state.sessions.get(body.session_id, key)
    if session is None:
        raise HTTPException(401, "Session expired.")
    if session.busy:
        raise HTTPException(409, "Wait for the current answer to finish.")
    if not state.sessions.allow_message(key):
        raise HTTPException(429, "Too many messages. Try again in a few minutes.")
    deps = Deps(state.gw2, state.wiki, key, session)
    return StreamingResponse(_stream(deps, body.message), media_type="application/x-ndjson")


async def _stream(deps: Deps, message: str):
    """Stream NDJSON events. Text is sent per line so aliases can be restored to real names."""
    session = deps.session
    session.busy = True
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
        session.busy = False


def _event(**data) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
