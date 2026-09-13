import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.atlas_provider import AtlasProvider

PG_ROW = {
    "isin": "geram18",
    "source": "estjt",
    "price": "23600000.0",  # asyncpg returns numeric columns as Decimal-like
    "updated_at": dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc),
    "payload": '{"name": "test"}',  # jsonb comes back as str, not a dict
}

REDIS_VALUE = json.dumps(
    {
        "time": "2026-09-13T15:30:00+03:30",
        "isin": "geram18",
        "source": "tabdeal",
        "price": 23590000.0,
        "created_at": "2026-09-13T15:30:00+03:30",
        "payload": {"name": "redis-test"},
    }
)


class FakeRecord(dict):
    """asyncpg.Record supports both dict-style and attribute-style
    access; a plain dict covers the ["key"] access the provider uses."""


def _fake_redis(scan_results: list[bytes], values: dict[bytes, str]):
    client = MagicMock()

    async def scan_iter(match=None):
        for key in scan_results:
            yield key

    client.scan_iter = scan_iter
    client.get = AsyncMock(side_effect=lambda key: values.get(key))
    return client


@pytest.mark.asyncio
async def test_latest_prefers_redis_and_falls_back_to_postgres_for_missing_sources():
    # Redis has "tabdeal" for geram18; Postgres separately has "estjt"
    # for geram18 -- both should come back, Redis's version for tabdeal
    # and Postgres's for estjt (not found in Redis).
    redis_client = _fake_redis(
        [b"atlas:raw:tabdeal:geram18"],
        {b"atlas:raw:tabdeal:geram18": REDIS_VALUE},
    )
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [FakeRecord(PG_ROW)]

    provider = AtlasProvider(redis_client, pg_pool)
    points = await provider.latest("geram18")

    by_source = {p.source: p for p in points}
    assert set(by_source) == {"tabdeal", "estjt"}
    assert by_source["tabdeal"].price == 23590000.0
    assert by_source["estjt"].price == 23600000.0
    assert isinstance(by_source["estjt"].price, float)


@pytest.mark.asyncio
async def test_latest_does_not_duplicate_a_source_found_in_both_stores():
    # Same source ("estjt") present in both Redis and Postgres -- only
    # the Redis version should be returned, not both.
    redis_value = json.dumps({**json.loads(REDIS_VALUE), "source": "estjt"})
    redis_client = _fake_redis(
        [b"atlas:raw:estjt:geram18"],
        {b"atlas:raw:estjt:geram18": redis_value},
    )
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [FakeRecord(PG_ROW)]  # also source="estjt"

    provider = AtlasProvider(redis_client, pg_pool)
    points = await provider.latest("geram18")

    assert len(points) == 1
    assert points[0].source == "estjt"
    assert points[0].price == 23590000.0  # the Redis value, not Postgres's


@pytest.mark.asyncio
async def test_list_latest_merges_both_stores_without_duplicates():
    redis_client = _fake_redis(
        [b"atlas:raw:tabdeal:geram18"],
        {b"atlas:raw:tabdeal:geram18": REDIS_VALUE},
    )
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [FakeRecord(PG_ROW)]  # source="estjt", different isin/source pair

    provider = AtlasProvider(redis_client, pg_pool)
    points = await provider.list_latest()

    assert {(p.isin, p.source) for p in points} == {
        ("geram18", "tabdeal"),
        ("geram18", "estjt"),
    }


@pytest.mark.asyncio
async def test_latest_handles_null_price_from_postgres():
    row = dict(PG_ROW)
    row["price"] = None
    redis_client = _fake_redis([], {})
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [FakeRecord(row)]

    provider = AtlasProvider(redis_client, pg_pool)
    points = await provider.latest("geram18")

    assert points[0].price is None
