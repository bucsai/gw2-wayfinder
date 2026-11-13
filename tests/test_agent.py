import json

import httpx
import pytest
import respx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import InstrumentationSettings
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
    "equipment_tabs": [{"is_active": True, "equipment": [{"id": 1, "slot": "Helm"}]}],
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
    if request.headers.get("Authorization") not in (None, f"Bearer {KEY}"):
        return httpx.Response(401, json={"text": "Invalid access token"})
    path = request.url.path
    if path == "/v2/characters" and request.url.params.get("ids") == "all":
        path = "/v2/characters/all"
    return httpx.Response(200, json=RESPONSES[path])


@pytest.fixture
async def deps():
    with respx.mock:
        respx.route(url__startswith=BASE_URL).mock(side_effect=gw2_api)
        with Cache(":memory:") as cache:
            async with httpx.AsyncClient() as http:
                gw2 = GW2Client(http, cache)
                session = await load_session(gw2, KEY)
                yield Deps(gw2, WikiClient(http, cache), KEY, session)


async def test_no_real_names_reach_the_model(deps, monkeypatch):
    # Traces (app/tracing.py) record what the model sees, so they must not contain real names either.
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    monkeypatch.setattr(agent_module.agent, "instrument", InstrumentationSettings(tracer_provider=provider))
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
    # 10 in the bank + 3 in Character_A's bags.
    assert '"total_per_item": [{"item": "Mystic Coin", "id": 2, "count": 13}]' in seen[1]
    assert deps.session.history

    traced = repr([(s.attributes, [e.attributes for e in s.events]) for s in spans.get_finished_spans()])
    assert "find_in_account" in traced
    assert "total_matches" in traced  # a tool result
    for name in REAL_NAMES:
        assert name.lower() not in traced.lower(), name


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
