from __future__ import annotations

import json

import asyncpg

from app.providers.base import PricePoint, PriceProvider

# Reads atlas.raw_ticks directly over SQL -- Nexus depends on Atlas's
# *data contract* (the table's columns), not on Atlas's Python code.
# The two are separate deployable projects; this is the only coupling
# between them, and it's intentionally thin.

_LATEST_FOR_ISIN_SQL = """
SELECT DISTINCT ON (source)
    isin, source, price,
    received_at AT TIME ZONE 'Asia/Tehran' AS updated_at,
    payload
FROM atlas.raw_ticks
WHERE isin = $1
ORDER BY source, time DESC
"""

_LIST_LATEST_SQL = """
SELECT DISTINCT ON (isin, source)
    isin, source, price,
    received_at AT TIME ZONE 'Asia/Tehran' AS updated_at,
    payload
FROM atlas.raw_ticks
WHERE ($1::text IS NULL OR source = $1)
ORDER BY isin, source, time DESC
"""


def _row_to_point(row: asyncpg.Record) -> PricePoint:
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
    """Reads latest price points straight out of atlas.raw_ticks."""

    name = "atlas"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def latest(self, isin: str) -> list[PricePoint]:
        rows = await self.pool.fetch(_LATEST_FOR_ISIN_SQL, isin)
        return [_row_to_point(r) for r in rows]

    async def list_latest(self, source: str | None = None) -> list[PricePoint]:
        rows = await self.pool.fetch(_LIST_LATEST_SQL, source)
        return [_row_to_point(r) for r in rows]
