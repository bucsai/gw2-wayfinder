import json

import httpx
import pytest
import respx
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app import agent as agent_module
from app.cache import Cache
from app.gw2 import BASE_URL, GW2Client
from app.sessions import load_session
from app.tools import Deps
from app.wiki import WikiClient

KEY = "ABCDEF01-2345-6789-ABCD-EF0123456789ABCDEF01-2345-6789-ABCD-EF0123456789"
GUILD_ID = "4BBB52AA-D768-4FC6-8EDE-C299F2822F0F"
REAL_NAMES = ["Wanderer", "Zoe Lightfoot", "Bob Stone", "Knights of Ascalon", GUILD_ID, KEY]

CHARACTER = {
    "name": "Zoe Lightfoot",
    "profession": "Ranger",
    "race": "Sylvari",
    "level": 80,
    "guild": GUILD_ID,
    "equipment": [{"id": 1, "slot": "Helm"}],
    "bags": [{"inventory": [{"id": 2, "count": 3}, None]}],
}
RESPONSES = {
    "/v2/tokeninfo": {"name": "Wanderer's key", "permissions": ["account", "characters", "inventories"]},
    "/v2/account": {"name": "Wanderer.1234", "id": GUILD_ID, "guilds": [GUILD_ID]},
    f"/v2/guild/{GUILD_ID}": {"name": "Knights of Ascalon", "tag": "KoA"},
    "/v2/characters": ["Zoe Lightfoot", "Bob Stone"],
    "/v2/characters/all": [CHARACTER, {**CHARACTER, "name": "Bob Stone", "bags": []}],
    "/v2/characters/Zoe Lightfoot": CHARACTER,
    "/v2/account/bank": [{"id": 2, "count": 10}, None],
    "/v2/account/materials": [],
    "/v2/account/inventory": [],
    "/v2/items": [{"id": 1, "name": "Bob Stone's Helm"}, {"id": 2, "name": "Mystic Coin"}],
}


def gw2_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v2/characters" and request.url.params.get("ids") == "all":
        path = "/v2/characters/all"
    return httpx.Response(200, json=RESPONSES[path])


@pytest.fixture
async def deps():
    with respx.mock:
        respx.route(url__startswith=BASE_URL).mock(side_effect=gw2_api)
        async with httpx.AsyncClient() as http:
            cache = Cache(":memory:")
            gw2 = GW2Client(http, cache)
            session = await load_session(gw2, KEY)
            yield Deps(gw2, WikiClient(http, cache), KEY, session)


async def test_no_real_names_reach_the_model(deps):
    seen = []

    async def model(messages, info: AgentInfo):
        seen.append(repr(messages) + repr(info.function_tools) + repr(info.instructions))
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            yield {
                0: DeltaToolCall("list_characters", "{}", tool_call_id="1"),
                1: DeltaToolCall("find_in_account", json.dumps({"name_contains": "coin"}), tool_call_id="2"),
                2: DeltaToolCall("character_details", json.dumps({"character": "Character_A"}), tool_call_id="3"),
                3: DeltaToolCall("account_overview", "{}", tool_call_id="4"),
            }
        else:
            yield "Character_A carries 3 Mystic Coin."

    message = f"What does zoe lightfoot carry? Guild Knights of Ascalon, key {KEY}"
    with agent_module.agent.override(model=FunctionModel(stream_function=model)):
        events = [e async for e in agent_module.run(deps, message)]

    text = "".join(v for kind, v in events if kind == "text")
    assert text == "Character_A carries 3 Mystic Coin."
    assert ("tool", "find_in_account") in events
    assert len(seen) == 2
    for name in REAL_NAMES:
        assert name.lower() not in "".join(seen).lower(), name
    # Item names that contain a character name are hidden too.
    assert '"item": "Character_B' in seen[1]
    assert "Mystic Coin" in seen[1]
    assert deps.session.history


async def test_tools_follow_key_permissions(deps):
    tools = []

    async def model(messages, info: AgentInfo):
        tools.extend(t.name for t in info.function_tools)
        yield "ok"

    with agent_module.agent.override(model=FunctionModel(stream_function=model)):
        [e async for e in agent_module.run(deps, "hi")]

    assert "find_in_account" in tools
    assert "wallet" not in tools
    assert "wizards_vault" not in tools
    assert "wiki_search" in tools
