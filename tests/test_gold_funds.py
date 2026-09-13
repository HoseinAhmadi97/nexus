import datetime as dt
import json
from unittest.mock import AsyncMock

import pytest

from app.providers.base import PricePoint
from app.services.gold_funds import (
    GOLD_FUND_TSE_IDS,
    _tickers_from_pandas_json,
    build_gold_funds_table,
)

NOW = dt.datetime(2026, 9, 13, 20, 0, tzinfo=dt.timezone.utc)

SOME_FUND_ID = next(iter(GOLD_FUND_TSE_IDS))

# pandas.DataFrame.to_json() shape: {col: {row_index: value}}
ALL_TICKERS_JSON = json.dumps(
    {
        "id": {"0": SOME_FUND_ID, "1": "not-a-gold-fund"},
        "isin": {"0": "IRTKMOFD0001", "1": "IRXXOTHER0001"},
        "symbol": {"0": "test_fund", "1": "other"},
        "last_trade": {"0": 636685, "1": 100},
        "ask_price_1": {"0": 636700, "1": 101},
        "bid_price_1": {"0": 636600, "1": 99},
        "value": {"0": 12345, "1": 1},
        "volume": {"0": 10, "1": 1},
        "nav": {"0": 636148.0, "1": 50.0},
    }
)


class FakeRecord(dict):
    pass


@pytest.mark.asyncio
async def test_tickers_from_pandas_json_reconstructs_rows():
    rows = _tickers_from_pandas_json(ALL_TICKERS_JSON)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "test_fund"
    assert rows[0]["last_trade"] == 636685


@pytest.mark.asyncio
async def test_build_gold_funds_table_filters_to_gold_funds_only():
    redis_client = AsyncMock()
    redis_client.get.return_value = ALL_TICKERS_JSON

    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [
        FakeRecord(
            fund="test_fund",
            sekke=0.1,
            shemsh=0.8,
            naghd=0.1,
            noghre=0.0,
            ayandeh=0.0,
        )
    ]

    atlas_provider = AsyncMock()
    atlas_provider.list_latest.side_effect = [
        [PricePoint(isin="IRTKMOFD0001", source="tadbir", price=636000.0, updated_at=NOW, payload={})],
        [PricePoint(isin="IRTKMOFD0001", source="farabi", price=636200.0, updated_at=NOW, payload={})],
    ]

    rows = await build_gold_funds_table(redis_client, pg_pool, atlas_provider)

    assert len(rows) == 1  # "not-a-gold-fund" row excluded
    row = rows[0]
    assert row.isin == "IRTKMOFD0001"
    assert row.last_trade == 636685
    assert row.nav_live == 636148.0
    assert row.nav_tadbir == 636000.0
    assert row.nav_farabi == 636200.0
    assert row.weights == {
        "sekke_weight": 0.1,
        "shemsh_weight": 0.8,
        "cash_weight": 0.1,
        "noghre_weight": 0.0,
        "ayandeh_weight": 0.0,
    }


@pytest.mark.asyncio
async def test_nominal_bubble_computed_from_live_last_trade_and_nav():
    redis_client = AsyncMock()
    redis_client.get.return_value = ALL_TICKERS_JSON
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = []
    atlas_provider = AsyncMock()
    atlas_provider.list_latest.return_value = []

    rows = await build_gold_funds_table(redis_client, pg_pool, atlas_provider)

    row = rows[0]
    assert row.nominal_bubble == pytest.approx((636685 / 636148.0) - 1)
    assert row.weights is None  # no weights row matched


@pytest.mark.asyncio
async def test_missing_redis_key_returns_empty_list_not_error():
    redis_client = AsyncMock()
    redis_client.get.return_value = None
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = []
    atlas_provider = AsyncMock()
    atlas_provider.list_latest.return_value = []

    rows = await build_gold_funds_table(redis_client, pg_pool, atlas_provider)

    assert rows == []
