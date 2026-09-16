from __future__ import annotations

import datetime as dt

import asyncpg
import redis.asyncio as redis

from app.providers.atlas_provider import AtlasProvider
from app.schemas import GoldFundRow, GoldSeries, GoldSeriesPoint, GoldSnapshot, GoldSummary
from app.services.gold_funds import build_gold_funds_table
from app.services.gold_market import LABELS, build_gold_market_table

# Iran has had no daylight saving time since 2022, so Tehran is a fixed
# offset. Every naive timestamp in atlas.raw_ticks and hist.* is Tehran
# local time.
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

#: A series is thinned to at most this many points -- enough for a
#: sparkline or a small chart, and it keeps the snapshot a few KB.
MAX_SERIES_POINTS = 60

#: A fund trade older than this means the market is not trading now.
MARKET_OPEN_WINDOW = dt.timedelta(minutes=15)

# The most recent day that has data, not "today": outside trading hours
# (and on holidays) a chart of the last session beats an empty one.
_ATLAS_LAST_DAY_SQL = """
SELECT time, price AS value
FROM atlas.raw_ticks
WHERE source = $1 AND isin = $2 AND price IS NOT NULL
  AND time >= (
    SELECT date_trunc('day', max(time)) FROM atlas.raw_ticks
    WHERE source = $1 AND isin = $2
  )
ORDER BY time
"""

_NAV_LAST_DAY_SQL = """
SELECT time, nav AS value
FROM hist.gold_fund_nav
WHERE source = $1 AND isin = $2 AND nav IS NOT NULL
  AND time >= (
    SELECT date_trunc('day', max(time)) FROM hist.gold_fund_nav
    WHERE source = $1 AND isin = $2
  )
ORDER BY time
"""


def downsample(points: list[GoldSeriesPoint], limit: int = MAX_SERIES_POINTS) -> list[GoldSeriesPoint]:
    """Evenly spaced subset that always keeps the first and last point,
    so the line still starts at the open and ends at the latest value."""
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


async def _read_series(pg_pool: asyncpg.Pool, sql: str, source: str, isin: str) -> list[GoldSeriesPoint]:
    rows = await pg_pool.fetch(sql, source, isin)
    points = [
        GoldSeriesPoint(time=r["time"].replace(tzinfo=TEHRAN), value=float(r["value"]))
        for r in rows
    ]
    return downsample(points)


def _parse_trade_time(text: str | None, now: dt.datetime) -> dt.datetime | None:
    if not text:
        return None
    hour, minute, second = (int(x) for x in text.split(":"))
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)


def summarize(funds: list[GoldFundRow], now: dt.datetime) -> GoldSummary:
    """`now` must be Tehran-aware; TSE trade times have no date, so they
    are read as today's."""
    bubbles = [(f.symbol, f.nominal_bubble) for f in funds if f.nominal_bubble is not None]
    changes = [f.change_pct for f in funds if f.change_pct is not None]
    times = [f.trade_time for f in funds if f.trade_time]
    last_trade_time = max(times) if times else None
    last_trade = _parse_trade_time(last_trade_time, now)
    market_open = (
        last_trade is not None
        and dt.timedelta(minutes=-1) <= now - last_trade <= MARKET_OPEN_WINDOW
    )

    hi = max(bubbles, key=lambda b: b[1]) if bubbles else None
    lo = min(bubbles, key=lambda b: b[1]) if bubbles else None
    return GoldSummary(
        fund_count=len(funds),
        avg_bubble=sum(b for _, b in bubbles) / len(bubbles) if bubbles else None,
        max_bubble={"symbol": hi[0], "bubble": hi[1]} if hi else None,
        min_bubble={"symbol": lo[0], "bubble": lo[1]} if lo else None,
        avg_change_pct=sum(changes) / len(changes) if changes else None,
        total_value=sum(f.value or 0 for f in funds),
        total_market_cap=sum(f.market_cap or 0 for f in funds),
        last_trade_time=last_trade_time,
        market_open=market_open,
    )


async def build_gold_snapshot(
    redis_client: redis.Redis,
    pg_pool: asyncpg.Pool,
    atlas_provider: AtlasProvider,
) -> GoldSnapshot:
    """Compose /v1/gold/market, /v1/gold/funds, their summary and two
    intraday series into the one document the website reads.
    """
    now = dt.datetime.now(TEHRAN)
    market = await build_gold_market_table(atlas_provider, pg_pool)
    funds = await build_gold_funds_table(redis_client, pg_pool, atlas_provider)

    series: dict[str, GoldSeries] = {}
    geram18 = await _read_series(pg_pool, _ATLAS_LAST_DAY_SQL, "estjt", "geram18")
    if geram18:
        series["geram18"] = GoldSeries(
            key="geram18", label=LABELS["geram18"], unit="IRT", source="estjt", points=geram18
        )

    # The featured NAV line is the largest fund by market cap, chosen from
    # the data rather than hard-coded, so it follows the market.
    sized = [f for f in funds if f.market_cap]
    if sized:
        featured = max(sized, key=lambda f: f.market_cap)
        nav = await _read_series(pg_pool, _NAV_LAST_DAY_SQL, "tadbir", featured.isin)
        if nav:
            series["nav"] = GoldSeries(
                key=featured.isin,
                label=featured.symbol,
                unit="IRR",
                source="tadbir",
                points=nav,
            )

    return GoldSnapshot(
        generated_at=now,
        summary=summarize(funds, now),
        market=market,
        funds=funds,
        series=series,
    )
