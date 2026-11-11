"""Agent tools. Every tool result is returned as JSON text that has passed through the pseudonymizer."""

import functools
import json
import re
from dataclasses import dataclass

from pydantic_ai import RunContext, Tool

from app.gw2 import GW2Client, GW2Error
from app.sessions import Session
from app.wiki import WikiClient, WikiUnavailable

STATIC_TTL = 7 * 24 * 3600
PRICE_TTL = 300


@dataclass
class Deps:
    gw2: GW2Client
    wiki: WikiClient
    key: str
    session: Session
    wiki_fetches: int = 0  # in this run


TOOLS: list[Tool] = []


def tool(*permissions: str):
    """Register a tool. It is hidden when the key lacks any of the given permissions."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(ctx: RunContext[Deps], *args, **kwargs):
            try:
                result = await func(ctx, *args, **kwargs)
            except GW2Error as e:
                result = {"error": str(e)}
            except WikiUnavailable:
                result = {"error": "The wiki is unavailable right now. Do not retry."}
            return ctx.deps.session.pseudo.scrub(json.dumps(result, ensure_ascii=False))

        async def prepare(ctx: RunContext[Deps], tool_def):
            return tool_def if set(permissions) <= ctx.deps.session.permissions else None

        TOOLS.append(Tool(wrapper, takes_ctx=True, prepare=prepare))
        return func

    return decorator


def coins(copper: int) -> str:
    gold, rest = divmod(copper, 10000)
    silver, copper = divmod(rest, 100)
    return " ".join(f"{v}{u}" for v, u in ((gold, "g"), (silver, "s"), (copper, "c")) if v) or "0c"


def clean(text: str | None) -> str:
    """Remove GW2 markup such as <c=@flavor>."""
    return re.sub(r"<[^>]+>", "", text or "")


async def item_names(gw2: GW2Client, ids) -> dict[int, str]:
    items = await gw2.get_many("/items", ids, STATIC_TTL)
    return {id_: item["name"] for id_, item in items.items()}


# Register tools by importing their modules.
from app.tools import account, public  # noqa: E402, F401
