"""Agent tools. Every tool result is returned as JSON text that has passed through the pseudonymizer."""

import functools
import json
import logging
import re
from dataclasses import dataclass

from pydantic_ai import RunContext, Tool

from app.gw2 import GW2Client, GW2Error
from app.sessions import Session
from app.wiki import WikiClient, WikiUnavailable

log = logging.getLogger("wayfinder")
STATIC_TTL = 7 * 24 * 3600
PRICE_TTL = 300


@dataclass
class Deps:
    gw2: GW2Client
    wiki: WikiClient
    key: str
    session: Session
    wiki_fetches: int = 0  # in this run


TOOLS: list[Tool[Deps]] = []


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
            except Exception as e:
                # Log the type only. The data can contain player names.
                log.error("Tool %s failed: %s", func.__name__, type(e).__name__)
                result = {"error": "This tool failed unexpectedly."}
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


def after_fees(price: int, listed: bool = False) -> int:
    """What the seller receives for one item at price after the Trading Post fees.

    The 5% listing fee is paid when listing, the 10% exchange fee when it sells.
    Each fee is rounded half up and at least 1 copper.
    listed means the listing fee was already paid.
    """
    listing_fee = 0 if listed else max(1, (price * 5 + 50) // 100)
    return max(0, price - listing_fee - max(1, (price * 10 + 50) // 100))


def clean(text: str | None) -> str:
    """Remove GW2 markup such as <c=@flavor>."""
    return re.sub(r"<[^>]+>", "", text or "")


async def item_names(gw2: GW2Client, ids) -> dict[int, str]:
    items = await gw2.get_many("/items", ids, STATIC_TTL)
    return {id_: item["name"] for id_, item in items.items()}


# Register tools by importing their modules.
from app.tools import account, public  # noqa: E402, F401
