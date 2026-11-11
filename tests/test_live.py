"""Calls the real GW2 API with a real key. Runs only when GW2_TEST_API_KEY is set.

The model is replaced with a fake one, so no Anthropic calls are made.
Failure messages never include account data.
"""

import json
import os

import httpx
import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app import agent as agent_module
from app.cache import Cache
from app.config import USER_AGENT
from app.gw2 import GW2Client
from app.sessions import load_session
from app.tools import Deps
from app.wiki import WikiClient

KEY = os.environ.get("GW2_TEST_API_KEY", "").strip()
pytestmark = pytest.mark.skipif(not KEY, reason="GW2_TEST_API_KEY is not set")

ARGS = {
    "character_details": {"character": "Character_A"},
    "find_in_account": {"name_contains": "coin"},
    "achievement_progress": {"ids": [1, 2, 3]},
    "get_items": {"ids": [19976]},
    "get_prices": {"ids": [19976]},
    "get_recipes": {"item_id": 19684},
    "get_achievements": {"ids": [1]},
}
SKIP = {"wiki_search", "wiki_page"}  # keep wiki traffic out of tests


async def test_all_tools_with_real_key():
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}) as http:
        cache = Cache(":memory:")
        session = await load_session(GW2Client(http, cache), KEY)
        deps = Deps(GW2Client(http, cache), WikiClient(http, cache), KEY, session)
        seen = []

        async def model(messages, info: AgentInfo):
            seen.append(repr(messages))
            if len(messages) == 1:
                tools = [t.name for t in info.function_tools if t.name not in SKIP]
                yield {
                    i: DeltaToolCall(name, json.dumps(ARGS.get(name, {})), tool_call_id=str(i))
                    for i, name in enumerate(tools)
                }
            else:
                yield "done"

        with agent_module.agent.override(model=FunctionModel(stream_function=model)):
            [e async for e in agent_module.run(deps, "test")]

    results = {
        p.tool_name: json.loads(str(p.content))
        for m in session.history
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    print("\npermissions:", sorted(session.permissions))
    for name, result in results.items():
        print(f"{name}: {_shape(result)}")

    failed = [name for name, result in results.items() if "error" in result]
    assert not failed, f"tools returned errors: {failed}"

    real_names = [r.lower() for r in session.pseudo.to_real.values()]
    leaked = [n for n in real_names if n in "".join(seen).lower()]
    assert not leaked, f"{len(leaked)} real name(s) reached the model"


def _shape(value, depth=0):
    """Describe the structure of a result without its values, plus which fields are empty."""
    if isinstance(value, dict):
        empty = [k for k, v in value.items() if v in (None, [], {}, "")]
        inner = {k: _shape(v, depth + 1) for k, v in value.items() if isinstance(v, (dict, list)) and v}
        return {"keys": list(value), "empty": empty, **({"nested": inner} if inner and depth < 2 else {})}
    if isinstance(value, list):
        return f"list[{len(value)}]" + (f" of {_shape(value[0], depth + 1)}" if value and depth < 2 else "")
    return type(value).__name__
