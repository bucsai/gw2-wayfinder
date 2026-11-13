from collections.abc import AsyncIterator

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.run import AgentRunResultEvent

from app import config
from app.tools import TOOLS, Deps

INSTRUCTIONS = """\
You are GW2 Wayfinder, a Guild Wars 2 assistant. You answer questions about the game and about the player's account.

- Use the tools to look up facts. Do not guess item ids, prices or account data.
- Use the totals and net values from tool results instead of adding up or calculating amounts yourself.
- Account, character and guild names are replaced with aliases such as Account_1, Character_A and Guild_A. \
Write aliases exactly as given. The user sees the real names.
- Use the wiki for guides, how to get things, and to find item ids by name. Search once, then read only the page you \
need. If the wiki is unavailable or the limit is reached, answer with what you have and do not retry.
- Keep answers short and practical. Use markdown lists or tables when they help.
"""

LIMITS = UsageLimits(request_limit=15, tool_calls_limit=25)

agent = Agent(
    config.MODEL,
    deps_type=Deps,
    instructions=INSTRUCTIONS,
    tools=TOOLS,
    model_settings=AnthropicModelSettings(anthropic_effort="low", anthropic_cache=True),
    defer_model_check=True,
)


@agent.instructions
def missing_permissions(ctx: RunContext[Deps]) -> str:
    wanted = {"account", "characters", "inventories", "wallet", "progression", "tradingpost", "builds", "unlocks"}
    missing = sorted(wanted - ctx.deps.session.permissions)
    if not missing:
        return ""
    return (
        f"The player's API key lacks these permissions: {', '.join(missing)}. "
        "If a question needs one, tell the player to create a key with it."
    )


async def run(deps: Deps, message: str) -> AsyncIterator[tuple[str, str]]:
    """Run the agent and yield ("text", chunk) and ("tool", name) events. Text is still pseudonymized."""
    session = deps.session
    prompt = session.pseudo.scrub(message)
    wrote_text = False
    async with agent.run_stream_events(
        prompt, deps=deps, message_history=session.history, usage_limits=LIMITS
    ) as events:
        async for event in events:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                if wrote_text:
                    yield "text", "\n\n"
                wrote_text = True
                yield "text", event.part.content
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                yield "text", event.delta.content_delta
            elif isinstance(event, FunctionToolCallEvent):
                yield "tool", event.part.tool_name
            elif isinstance(event, AgentRunResultEvent):
                session.history = event.result.all_messages()
