from collections import Counter
from urllib.parse import quote

from pydantic_ai import RunContext

from app.tools import STATIC_TTL, Deps, after_fees, coins, item_names, tool


async def _get(ctx: RunContext[Deps], path: str, params: dict | None = None):
    return await ctx.deps.gw2.get(path, key=ctx.deps.key, params=params)


@tool("account")
async def account_overview(ctx: RunContext[Deps]) -> dict:
    """Get an overview of the player's account: age, expansions, fractal level, WvW rank, achievement points, guilds."""
    account = await _get(ctx, "/account")
    session = ctx.deps.session
    overview = {
        "account": session.pseudo.alias(account["name"], "account"),
        "created": account.get("created"),
        "played_hours": round(account.get("age", 0) / 3600),
        "access": account.get("access", []),
        "commander": account.get("commander"),
        "fractal_level": account.get("fractal_level"),
        "wvw_rank": (account.get("wvw") or {}).get("rank", account.get("wvw_rank")),
        "daily_ap": account.get("daily_ap"),
        "monthly_ap": account.get("monthly_ap"),
        "guilds": [session.guilds[g] for g in account.get("guilds", []) if g in session.guilds],
        "key_permissions": sorted(session.permissions),
    }
    if "progression" in session.permissions:
        mastery = await _get(ctx, "/account/mastery/points")
        overview["mastery_points"] = mastery.get("totals", [])
    return overview


@tool("account", "characters")
async def list_characters(ctx: RunContext[Deps]) -> dict:
    """List the player's characters with profession, race, level and crafting."""
    characters = await _get(ctx, "/characters", {"ids": "all"})
    pseudo = ctx.deps.session.pseudo
    return {
        "characters": [
            {
                "character": pseudo.alias(c["name"], "character"),
                "profession": c["profession"],
                "race": c["race"],
                "level": c["level"],
                "played_hours": round(c.get("age", 0) / 3600),
                "created": c.get("created"),
                "deaths": c.get("deaths"),
                "crafting": [f"{d['discipline']} {d['rating']}" for d in c.get("crafting", [])],
            }
            for c in characters
        ]
    }


@tool("account", "characters")
async def character_details(ctx: RunContext[Deps], character: str) -> dict:
    """Get a character's equipped gear, active build and skill bars. character is the alias, e.g. Character_A."""
    name = ctx.deps.session.pseudo.real(character)
    if name is None:
        return {"error": f"Unknown character {character!r}. Use list_characters."}
    gw2 = ctx.deps.gw2
    c = await _get(ctx, f"/characters/{quote(name)}")
    # The active equipment tab lists every slot, including Legendary Armory items.
    tab = next((t for t in c.get("equipment_tabs", []) if t.get("is_active")), {})
    equipment = [e for e in tab.get("equipment", []) if e.get("slot")]
    upgrade_ids = [u for e in equipment for u in e.get("upgrades", []) + e.get("infusions", [])]
    names = await item_names(gw2, [e["id"] for e in equipment] + upgrade_ids)

    result = {
        "character": character,
        "profession": c["profession"],
        "level": c["level"],
        "equipment": [
            {
                "slot": e["slot"],
                "item": names.get(e["id"], e["id"]),
                "upgrades": [names.get(u, u) for u in e.get("upgrades", [])],
                "infusions": [names.get(u, u) for u in e.get("infusions", [])],
            }
            for e in equipment
        ],
    }
    build = next((t["build"] for t in c.get("build_tabs", []) if t.get("is_active")), None)
    if build:
        specs = [s for s in build.get("specializations", []) if s.get("id")]
        spec_names = await gw2.get_many("/specializations", [s["id"] for s in specs], STATIC_TTL)
        traits = await gw2.get_many("/traits", [t for s in specs for t in s.get("traits", []) if t], STATIC_TTL)
        result["build"] = [
            {
                "specialization": spec_names.get(s["id"], {}).get("name", s["id"]),
                "traits": [traits.get(t, {}).get("name", t) for t in s.get("traits", []) if t],
            }
            for s in specs
        ]
        bars = {k: build.get(k) or {} for k in ("skills", "aquatic_skills")}
        skill_ids = [
            i for bar in bars.values() for i in [bar.get("heal"), *bar.get("utilities", []), bar.get("elite")] if i
        ]
        # Revenant only. Legends have no name; their swap skill carries it.
        slots = {k: [i for i in build.get(k) or [] if i] for k in ("legends", "aquatic_legends")}
        legends = await gw2.get_many("/legends", slots["legends"] + slots["aquatic_legends"], STATIC_TTL)
        skills = await gw2.get_many("/skills", skill_ids + [g["swap"] for g in legends.values()], STATIC_TTL)

        def skill(id_):
            return skills.get(id_, {}).get("name", id_) if id_ else None

        for k, bar in bars.items():
            result[k] = {
                "heal": skill(bar.get("heal")),
                "utilities": [skill(i) for i in bar.get("utilities", [])],
                "elite": skill(bar.get("elite")),
            }
        for k, ids in slots.items():
            if ids:
                result[k] = [skill(legends.get(i, {}).get("swap")) or i for i in ids]

        # Ranger only.
        pets = {k: [i for i in v if i] for k, v in (build.get("pets") or {}).items()}
        pet_info = await gw2.get_many("/pets", [i for v in pets.values() for i in v], STATIC_TTL)
        if pet_info:
            result["pets"] = {k: [pet_info.get(i, {}).get("name", i) for i in v] for k, v in pets.items()}
    return result


@tool("account", "wallet")
async def wallet(ctx: RunContext[Deps]) -> dict:
    """Get the player's currencies (gold, karma, laurels and so on)."""
    entries = await _get(ctx, "/account/wallet")
    currencies = await ctx.deps.gw2.get_many("/currencies", [e["id"] for e in entries], STATIC_TTL)
    return {
        "wallet": [
            {
                "currency": currencies.get(e["id"], {}).get("name", e["id"]),
                "amount": coins(e["value"]) if e["id"] == 1 else e["value"],
            }
            for e in entries
        ]
    }


@tool("account", "inventories")
async def find_in_account(ctx: RunContext[Deps], name_contains: str = "", item_ids: list[int] | None = None) -> dict:
    """Find items the player owns in the bank, material storage, shared inventory and character bags.

    Match by part of the item name, by item ids, or both. total_per_item adds up the counts over all locations.
    """
    counts: Counter = Counter()

    def add(slots, location):
        for slot in slots or []:
            if slot:
                counts[(slot["id"], location)] += slot.get("count", 1)

    add(await _get(ctx, "/account/bank"), "Bank")
    add(await _get(ctx, "/account/materials"), "Material storage")
    add(await _get(ctx, "/account/inventory"), "Shared inventory")
    if "characters" in ctx.deps.session.permissions:
        pseudo = ctx.deps.session.pseudo
        for c in await _get(ctx, "/characters", {"ids": "all"}):
            for bag in c.get("bags") or []:
                add((bag or {}).get("inventory"), pseudo.alias(c["name"], "character"))

    names = await item_names(ctx.deps.gw2, {id_ for id_, _ in counts})
    needle = name_contains.strip().lower()
    matches = [
        {"item": names.get(id_, id_), "id": id_, "location": location, "count": count}
        for (id_, location), count in counts.items()
        if (item_ids and id_ in item_ids) or (needle and needle in names.get(id_, "").lower())
    ]
    totals: Counter = Counter()
    for m in matches:
        totals[(m["item"], m["id"])] += m["count"]
    return {
        "matches": sorted(matches, key=lambda m: -m["count"])[:50],
        "total_matches": len(matches),
        "total_per_item": [{"item": item, "id": id_, "count": n} for (item, id_), n in totals.most_common(50)],
    }


@tool("account", "tradingpost")
async def trading_post_orders(ctx: RunContext[Deps]) -> dict:
    """Get the player's current Trading Post buy orders, listings and items waiting for pickup.

    listings_net_total is what the listings pay out after the 10% exchange fee (the listing fee is already paid).
    """
    buys = await _get(ctx, "/commerce/transactions/current/buys")
    sells = await _get(ctx, "/commerce/transactions/current/sells")
    delivery = await _get(ctx, "/commerce/delivery")
    names = await item_names(ctx.deps.gw2, [t["item_id"] for t in buys + sells] + [i["id"] for i in delivery["items"]])

    def orders(transactions):
        return [
            {
                "item": names.get(t["item_id"], t["item_id"]),
                "price": coins(t["price"]),
                "quantity": t["quantity"],
                "total": coins(t["price"] * t["quantity"]),
            }
            for t in transactions
        ]

    return {
        "buy_orders": orders(buys),
        "buy_orders_total": coins(sum(t["price"] * t["quantity"] for t in buys)),
        "listings": orders(sells),
        "listings_total": coins(sum(t["price"] * t["quantity"] for t in sells)),
        "listings_net_total": coins(sum(after_fees(t["price"], listed=True) * t["quantity"] for t in sells)),
        "pickup_coins": coins(delivery["coins"]),
        "pickup_items": [{"item": names.get(i["id"], i["id"]), "count": i["count"]} for i in delivery["items"]],
    }


@tool("account", "progression")
async def achievement_progress(ctx: RunContext[Deps], ids: list[int]) -> dict:
    """Get the player's progress on achievements by achievement id."""
    progress = {a["id"]: a for a in await _get(ctx, "/account/achievements", {"ids": ",".join(map(str, ids))})}
    info = await ctx.deps.gw2.get_many("/achievements", ids, STATIC_TTL)
    return {
        "achievements": [
            {
                "id": id_,
                "name": info.get(id_, {}).get("name"),
                "done": progress.get(id_, {}).get("done", False),
                "current": progress.get(id_, {}).get("current", 0),
                "max": progress.get(id_, {}).get("max"),
                "repeated": progress.get(id_, {}).get("repeated"),
            }
            for id_ in ids
        ]
    }


@tool("account", "progression")
async def wizards_vault(ctx: RunContext[Deps]) -> dict:
    """Get the player's Wizard's Vault daily, weekly and special objectives with progress."""
    result = {}
    for period in ("daily", "weekly", "special"):
        data = await _get(ctx, f"/account/wizardsvault/{period}")
        result[period] = [
            {
                "objective": o["title"],
                "track": o.get("track"),
                "acclaim": o.get("acclaim"),
                "progress": f"{o.get('progress_current', 0)}/{o.get('progress_complete', 0)}",
                "claimed": o.get("claimed"),
            }
            for o in data.get("objectives", [])
        ]
    return result


@tool("account", "progression")
async def weekly_clears(ctx: RunContext[Deps]) -> dict:
    """Get what the player completed since the last reset: raids, dungeons, world bosses, map chests."""
    return {
        "raid_encounters": await _get(ctx, "/account/raids"),
        "dungeon_paths": await _get(ctx, "/account/dungeons"),
        "world_bosses": await _get(ctx, "/account/worldbosses"),
        "map_chests": await _get(ctx, "/account/mapchests"),
    }
