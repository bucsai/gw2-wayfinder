from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.anonymize import Pseudonymizer
from app.cache import Cache
from app.gw2 import BASE_URL, GW2Client
from app.tools import after_fees
from app.tools.account import character_details, trading_post_orders
from app.tools.public import get_prices

ITEMS = [{"id": 1, "name": "Mystic Coin"}, {"id": 2, "name": "Glob of Ectoplasm"}]


@pytest.fixture
async def ctx():
    with respx.mock:
        respx.get(f"{BASE_URL}/items").respond(200, json=ITEMS)
        with Cache(":memory:") as cache:
            async with httpx.AsyncClient() as http:
                ctx: Any = SimpleNamespace(deps=SimpleNamespace(gw2=GW2Client(http, cache), key="KEY"))
                yield ctx


def test_after_fees():
    assert after_fees(10000) == 8500  # 5% listing + 10% exchange
    assert after_fees(10000, listed=True) == 9000
    assert after_fees(1) == 0  # each fee is at least 1 copper, never below 0
    assert after_fees(10) == 8  # fees round half up: 0.5c -> 1c, 1c
    assert after_fees(150) == 127  # 7.5c -> 8c, 15c


async def test_get_prices_adds_net_values_and_totals(ctx):
    respx.get(f"{BASE_URL}/commerce/prices").respond(
        200,
        json=[
            {"id": 1, "buys": {"unit_price": 10000, "quantity": 5}, "sells": {"unit_price": 12000, "quantity": 7}},
            {"id": 2, "buys": {"unit_price": 2000, "quantity": 5}, "sells": {"unit_price": 0, "quantity": 0}},
        ],
    )
    result = await get_prices(ctx, [1, 2], {1: 3, 2: 10})

    coin = result["prices"][0]
    assert coin["buy_net"] == "85s"
    assert coin["sell_net"] == "1g 2s"
    assert coin["quantity"] == 3
    assert coin["sell_net_total"] == "3g 6s"
    assert result["prices"][1]["sell_net"] == "0c"  # no listings
    assert list(result["totals"]) == ["buy", "buy_net", "sell", "sell_net"]
    assert result["totals"] == {"buy": "5g", "buy_net": "4g 25s", "sell": "3g 60s", "sell_net": "3g 6s"}


async def test_get_prices_without_quantities_has_no_totals(ctx):
    respx.get(f"{BASE_URL}/commerce/prices").respond(
        200, json=[{"id": 1, "buys": {"unit_price": 100, "quantity": 1}, "sells": {"unit_price": 200, "quantity": 1}}]
    )
    result = await get_prices(ctx, [1])
    assert "totals" not in result
    assert "quantity" not in result["prices"][0]


async def test_trading_post_orders_adds_totals(ctx):
    respx.get(f"{BASE_URL}/commerce/transactions/current/buys").respond(
        200, json=[{"item_id": 1, "price": 9000, "quantity": 2}, {"item_id": 2, "price": 2000, "quantity": 10}]
    )
    respx.get(f"{BASE_URL}/commerce/transactions/current/sells").respond(
        200, json=[{"item_id": 1, "price": 13000, "quantity": 4}]
    )
    respx.get(f"{BASE_URL}/commerce/delivery").respond(200, json={"coins": 0, "items": []})
    result = await trading_post_orders(ctx)

    assert result["buy_orders"][0]["total"] == "1g 80s"
    assert result["buy_orders_total"] == "3g 80s"
    assert result["listings_total"] == "5g 20s"
    assert result["listings_net_total"] == "4g 68s"  # only the 10% exchange fee is still due


async def test_character_details_returns_skill_bars(ctx):
    pseudo = Pseudonymizer()
    alias = pseudo.alias("Real Name", "character")
    ctx.deps.session = SimpleNamespace(pseudo=pseudo)
    build = {
        "specializations": [],
        "skills": {"heal": 10, "utilities": [11, 12, None], "elite": 13},
        "aquatic_skills": {"heal": 10, "utilities": [11, None, None], "elite": None},
    }
    respx.get(f"{BASE_URL}/characters/Real%20Name").respond(
        200,
        json={"profession": "Warrior", "level": 80, "build_tabs": [{"is_active": True, "build": build}]},
    )
    names = {10: "Healing Signet", 11: "Bulls Charge", 12: "Shake It Off!", 13: "Signet of Rage"}
    respx.get(f"{BASE_URL}/skills").respond(200, json=[{"id": i, "name": n} for i, n in names.items()])
    result = await character_details(ctx, alias)

    assert result["skills"] == {
        "heal": "Healing Signet",
        "utilities": ["Bulls Charge", "Shake It Off!", None],
        "elite": "Signet of Rage",
    }
    assert result["aquatic_skills"] == {
        "heal": "Healing Signet",
        "utilities": ["Bulls Charge", None, None],
        "elite": None,
    }
