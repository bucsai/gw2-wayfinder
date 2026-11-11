from pydantic_ai import RunContext

from app.tools import PRICE_TTL, STATIC_TTL, Deps, clean, coins, item_names, tool

WIKI_FETCHES_PER_RUN = 3
WIKI_FETCHES_PER_SESSION = 20


class WikiLimitReached(Exception):
    pass


@tool()
async def get_items(ctx: RunContext[Deps], ids: list[int]) -> dict:
    """Get item details by item id."""
    items = await ctx.deps.gw2.get_many("/items", ids, STATIC_TTL)
    return {
        "items": [
            {
                "id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "rarity": item["rarity"],
                "level": item["level"],
                "vendor_value": coins(item.get("vendor_value", 0)),
                "flags": item.get("flags", []),
                "description": clean(item.get("description")),
            }
            for item in items.values()
        ],
        "unknown_ids": [i for i in ids if i not in items],
    }


@tool()
async def get_prices(ctx: RunContext[Deps], ids: list[int]) -> dict:
    """Get current Trading Post prices by item id. buy is the highest buy order, sell is the lowest listing."""
    prices = await ctx.deps.gw2.get_many("/commerce/prices", ids, PRICE_TTL)
    names = await item_names(ctx.deps.gw2, prices)
    return {
        "prices": [
            {
                "id": id_,
                "name": names.get(id_),
                "buy": coins(p["buys"]["unit_price"]),
                "buy_orders": p["buys"]["quantity"],
                "sell": coins(p["sells"]["unit_price"]),
                "listings": p["sells"]["quantity"],
            }
            for id_, p in prices.items()
        ],
        "not_tradable": [i for i in ids if i not in prices],
    }


@tool()
async def get_recipes(ctx: RunContext[Deps], item_id: int) -> dict:
    """Get the crafting recipes that produce an item."""
    gw2 = ctx.deps.gw2
    recipe_ids = await gw2.get_cached("/recipes/search", STATIC_TTL, {"output": item_id})
    recipes = (await gw2.get_many("/recipes", recipe_ids, STATIC_TTL)).values()
    ingredient_ids = [i["id"] for r in recipes for i in r["ingredients"] if i["type"] == "Item"]
    names = await item_names(gw2, ingredient_ids)
    return {
        "recipes": [
            {
                "disciplines": r["disciplines"],
                "min_rating": r["min_rating"],
                "output_count": r["output_item_count"],
                "ingredients": [
                    {"name": names.get(i["id"], f"{i['type']} {i['id']}"), "id": i["id"], "count": i["count"]}
                    for i in r["ingredients"]
                ],
            }
            for r in recipes
        ]
    }


@tool()
async def get_achievements(ctx: RunContext[Deps], ids: list[int]) -> dict:
    """Get achievement details by achievement id."""
    achievements = await ctx.deps.gw2.get_many("/achievements", ids, STATIC_TTL)
    return {
        "achievements": [
            {
                "id": a["id"],
                "name": a["name"],
                "description": clean(a.get("description")),
                "requirement": clean(a.get("requirement")),
                "tiers": a.get("tiers", []),
                "rewards": a.get("rewards", []),
            }
            for a in achievements.values()
        ]
    }


def _count_wiki_fetch(deps: Deps):
    def on_fetch():
        if deps.wiki_fetches >= WIKI_FETCHES_PER_RUN or deps.session.wiki_fetches >= WIKI_FETCHES_PER_SESSION:
            raise WikiLimitReached()
        deps.wiki_fetches += 1
        deps.session.wiki_fetches += 1

    return on_fetch


@tool()
async def wiki_search(ctx: RunContext[Deps], query: str) -> dict:
    """Search the official GW2 Wiki. Returns page titles with short snippets."""
    try:
        return {"results": await ctx.deps.wiki.search(query, _count_wiki_fetch(ctx.deps))}
    except WikiLimitReached:
        return {"error": "Wiki request limit reached. Answer with what you have."}


@tool()
async def wiki_page(ctx: RunContext[Deps], title: str) -> dict:
    """Read a GW2 Wiki page by exact title. Returns the page text and any game ids from its infobox."""
    try:
        page = await ctx.deps.wiki.page(title, _count_wiki_fetch(ctx.deps))
    except WikiLimitReached:
        return {"error": "Wiki request limit reached. Answer with what you have."}
    return page or {"error": "Page not found. Use wiki_search to find the exact title."}
