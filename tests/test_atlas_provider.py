import datetime as dt
from unittest.mock import AsyncMock

import pytest

from app.providers.atlas_provider import AtlasProvider

SAMPLE_ROW = {
    "isin": "geram18",
    "source": "estjt",
    "price": "23600000.0",  # asyncpg returns numeric columns as Decimal-like
    "updated_at": dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc),
    "payload": '{"name": "test"}',  # jsonb comes back as str, not a dict
}


class FakeRecord(dict):
    """asyncpg.Record supports both dict-style and attribute-style
    access; a plain dict covers the ["key"] access _row_to_point uses."""


@pytest.mark.asyncio
async def test_latest_converts_decimal_price_and_parses_jsonb_payload():
    pool = AsyncMock()
    pool.fetch.return_value = [FakeRecord(SAMPLE_ROW)]
    provider = AtlasProvider(pool)

    points = await provider.latest("geram18")

    assert len(points) == 1
    p = points[0]
    assert p.isin == "geram18"
    assert p.source == "estjt"
    assert p.price == 23600000.0
    assert isinstance(p.price, float)
    assert p.payload == {"name": "test"}


@pytest.mark.asyncio
async def test_latest_handles_null_price():
    row = dict(SAMPLE_ROW)
    row["price"] = None
    pool = AsyncMock()
    pool.fetch.return_value = [FakeRecord(row)]
    provider = AtlasProvider(pool)

    points = await provider.latest("geram18")

    assert points[0].price is None


@pytest.mark.asyncio
async def test_list_latest_passes_source_filter_through_to_the_query():
    pool = AsyncMock()
    pool.fetch.return_value = []
    provider = AtlasProvider(pool)

    await provider.list_latest(source="wallex")

    args, _ = pool.fetch.call_args
    assert args[1] == "wallex"
