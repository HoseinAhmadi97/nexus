import datetime as dt
from unittest.mock import AsyncMock

import pytest

from app.providers.base import PricePoint
from app.services.gold_market import build_gold_market_table

NOW = dt.datetime(2026, 9, 13, 20, 0, tzinfo=dt.timezone.utc)


def _point(isin, source, price) -> PricePoint:
    return PricePoint(isin=isin, source=source, price=price, updated_at=NOW, payload={})


ALL_POINTS = [
    _point("ons_tala", "estjt", 4348.0),
    _point("geram18", "estjt", 23600000.0),
    _point("geram18", "tabdeal", 23590000.0),  # a second source for the same isin -- ignored
    _point("geram24", "estjt", 31500000.0),
    _point("sekee_new", "estjt", 23900000.0),
    _point("GoldCoin", "ime", 2400000000.0),
    _point("GoldBar", "ime", 1050000000.0),
    _point("USDTTMN", "wallex", 233000.0),
    _point("dollar", "tabdeal", 231000.0),
]


@pytest.mark.asyncio
async def test_builds_one_row_per_confirmed_symbol():
    provider = AsyncMock()
    provider.latest_for.return_value = ALL_POINTS

    rows = await build_gold_market_table(provider)

    symbols = {r.symbol for r in rows}
    assert symbols == {"ons", "geram18", "geram24", "sekee", "govahi_sekke", "govahi_shemsh", "dollar"}


@pytest.mark.asyncio
async def test_geram18_comes_from_estjt_specifically_not_tabdeal():
    provider = AsyncMock()
    provider.latest_for.return_value = ALL_POINTS

    rows = await build_gold_market_table(provider)

    geram18 = next(r for r in rows if r.symbol == "geram18")
    assert geram18.source == "estjt"
    assert geram18.price == 23600000.0


@pytest.mark.asyncio
async def test_dollar_is_averaged_across_wallex_and_tabdeal_with_components_shown():
    provider = AsyncMock()
    provider.latest_for.return_value = ALL_POINTS

    rows = await build_gold_market_table(provider)

    dollar = next(r for r in rows if r.symbol == "dollar")
    assert dollar.price == (233000.0 + 231000.0) / 2
    assert dollar.source == "avg(wallex,tabdeal)"
    assert {c["source"] for c in dollar.components} == {"wallex", "tabdeal"}


@pytest.mark.asyncio
async def test_missing_instrument_is_skipped_not_errored():
    provider = AsyncMock()
    provider.latest_for.return_value = [_point("ons_tala", "estjt", 4348.0)]

    rows = await build_gold_market_table(provider)

    assert {r.symbol for r in rows} == {"ons"}
