from __future__ import annotations

import datetime as dt
import json

import asyncpg
import redis.asyncio as redis

from app.providers.base import PricePoint, PriceProvider

# Redis is the primary read path: Atlas's RedisSink already maintains a
# no-TTL, continuously-overwritten "latest value" snapshot at
# atlas:raw:{source}:{isin} specifically so a fast current-value read
# never has to touch Postgres. Reading Redis first also means Nexus is
# depending on Atlas's *keyspace* convention here, the same way
# _PG_LATEST_SQL depends on Atlas's *table* convention -- both are data
# contracts between the two projects, not code coupling.
#
# Postgres (atlas.raw_ticks) is a fallback, not a second primary path:
# it's only consulted for a source `latest()`/`list_latest()` didn't
# find in Redis (e.g. Redis was flushed, or a source has genuinely
# never run since). Under normal operation this fallback rarely fires
# -- Redis keys never expire, so a source missing from Redis usually
# means it's missing from everywhere, not that Postgres has fresher data.

_REDIS_KEY_PREFIX = "atlas:raw"

_PG_LATEST_FOR_ISIN_SQL = """
SELECT DISTINCT ON (source)
    isin, source, price,
    received_at AT TIME ZONE 'Asia/Tehran' AS updated_at,
    payload
FROM atlas.raw_ticks
WHERE isin = $1
ORDER BY source, time DESC
"""

_PG_LIST_LATEST_SQL = """
SELECT DISTINCT ON (isin, source)
    isin, source, price,
    received_at AT TIME ZONE 'Asia/Tehran' AS updated_at,
    payload
FROM atlas.raw_ticks
WHERE ($1::text IS NULL OR source = $1)
ORDER BY isin, source, time DESC
"""


# Postgres fallback for specific (source, isin) pairs Redis didn't have.
# Filtering to the pairs up front keeps this off the whole-table scan
# _PG_LIST_LATEST_SQL does.
_PG_LATEST_FOR_PAIRS_SQL = """
SELECT DISTINCT ON (source, isin)
    isin, source, price,
    received_at AT TIME ZONE 'Asia/Tehran' AS updated_at,
    payload
FROM atlas.raw_ticks
WHERE (source, isin) IN (SELECT * FROM unnest($1::text[], $2::text[]))
ORDER BY source, isin, time DESC
"""


def _point_from_redis_value(raw: str | bytes) -> PricePoint:
    data = json.loads(raw)
    return PricePoint(
        isin=data["isin"],
        source=data["source"],
        price=data["price"],
        updated_at=dt.datetime.fromisoformat(data["time"]),
        payload=data["payload"],
    )


def _point_from_pg_row(row: asyncpg.Record) -> PricePoint:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    price = row["price"]  # numeric column -> asyncpg returns Decimal, not float
    return PricePoint(
        isin=row["isin"],
        source=row["source"],
        price=float(price) if price is not None else None,
        updated_at=row["updated_at"],
        payload=payload,
    )


class AtlasProvider(PriceProvider):
    """Reads Atlas's data: Redis (latest-snapshot cache) first, falling
    back to Postgres (atlas.raw_ticks) only for sources Redis doesn't
    have. See the module docstring for why it's split this way."""

    name = "atlas"

    def __init__(self, redis_client: redis.Redis, pg_pool: asyncpg.Pool) -> None:
        self.redis = redis_client
        self.pg_pool = pg_pool

    async def _redis_scan(self, pattern: str) -> list[PricePoint]:
        points = []
        async for key in self.redis.scan_iter(match=pattern):
            raw = await self.redis.get(key)
            if raw is not None:
                points.append(_point_from_redis_value(raw))
        return points

    async def latest(self, isin: str) -> list[PricePoint]:
        redis_points = await self._redis_scan(f"{_REDIS_KEY_PREFIX}:*:{isin}")
        found_sources = {p.source for p in redis_points}

        pg_rows = await self.pg_pool.fetch(_PG_LATEST_FOR_ISIN_SQL, isin)
        fallback = [
            _point_from_pg_row(r) for r in pg_rows if r["source"] not in found_sources
        ]
        return redis_points + fallback

    async def latest_for(self, keys: list[tuple[str, str]]) -> list[PricePoint]:
        """Latest point for each given (source, isin) pair.

        The targeted read for composed endpoints that know exactly which
        instruments they need: one Redis MGET instead of scanning every
        key, and Postgres only for pairs Redis didn't have -- the same
        Redis-first rule as latest()/list_latest(). list_latest() with no
        source filter scans all of atlas.raw_ticks on every call (~0.7 s
        at 160k rows, growing with the table), which a timer-driven
        snapshot can't afford.
        """
        keys = list(dict.fromkeys(keys))
        if not keys:
            return []
        raws = await self.redis.mget([f"{_REDIS_KEY_PREFIX}:{s}:{i}" for s, i in keys])
        points = [_point_from_redis_value(raw) for raw in raws if raw is not None]
        missing = [key for key, raw in zip(keys, raws) if raw is None]
        if missing:
            rows = await self.pg_pool.fetch(
                _PG_LATEST_FOR_PAIRS_SQL, [s for s, _ in missing], [i for _, i in missing]
            )
            points.extend(_point_from_pg_row(r) for r in rows)
        return points

    async def list_latest(self, source: str | None = None) -> list[PricePoint]:
        pattern = f"{_REDIS_KEY_PREFIX}:{source}:*" if source else f"{_REDIS_KEY_PREFIX}:*"
        redis_points = await self._redis_scan(pattern)
        found = {(p.isin, p.source) for p in redis_points}

        pg_rows = await self.pg_pool.fetch(_PG_LIST_LATEST_SQL, source)
        fallback = [
            _point_from_pg_row(r) for r in pg_rows if (r["isin"], r["source"]) not in found
        ]
        return redis_points + fallback
