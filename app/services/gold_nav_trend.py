from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg
import redis.asyncio as redis

from app.providers.atlas_provider import AtlasProvider
from app.schemas import GoldFundRow, GoldNavTrend, GoldNavTrendFund
from app.services.gold_funds import build_gold_funds_table
from app.services.gold_snapshot import TEHRAN

#: NAV providers publish about once a minute; 5-minute buckets keep a
#: full session (~9:00-15:30) under 80 points per fund.
BUCKET_MINUTES = 5
NAV_SOURCE = "tadbir"

# Every fund's NAV on the most recent day the source has data for.
# hist.gold_fund_nav.time is naive Tehran time.
_NAV_LAST_DAY_SQL = """
SELECT isin, time, nav
FROM hist.gold_fund_nav
WHERE source = $1 AND isin = ANY($2::text[]) AND nav IS NOT NULL
  AND time >= (
    SELECT date_trunc('day', max(time)) FROM hist.gold_fund_nav
    WHERE source = $1 AND isin = ANY($2::text[])
  )
ORDER BY time
"""

# Each fund's last NAV before the trend's day: yesterday's close, the base
# for "change today". Bounded to 10 days back so weekends and holidays
# still find one.
_NAV_PREV_CLOSE_SQL = """
SELECT DISTINCT ON (isin) isin, nav
FROM hist.gold_fund_nav
WHERE source = $1 AND isin = ANY($2::text[]) AND nav IS NOT NULL
  AND time < $3 AND time >= $3 - interval '10 days'
ORDER BY isin, time DESC
"""


def _bucket(t: dt.datetime) -> dt.datetime:
    return t.replace(minute=t.minute - t.minute % BUCKET_MINUTES, second=0, microsecond=0)


def nav_trend_from_rows(
    funds: list[GoldFundRow],
    rows: list[Any],
    now: dt.datetime,
    prev_closes: dict[str, float] | None = None,
) -> GoldNavTrend:
    """Put every fund on one shared time grid.

    Each bucket holds the fund's last NAV inside it, carried forward
    through buckets with no update so lines don't break mid-session.
    Buckets before a fund's first NAV of the day stay None rather than
    being back-filled -- a line should start where the data starts.
    Funds keep the order given (largest market cap first).

    `change_pct` is measured against the fund's previous-day close when
    one exists, else against its first NAV of the day. The previous close
    is the right base: NAV publishers often repeat yesterday's value for
    the first part of a session, so "first NAV of the day" is frequently
    just yesterday's number anyway -- and when it isn't, change since the
    first update would hide the overnight move.
    """
    prev_closes = prev_closes or {}
    by_isin: dict[str, dict[dt.datetime, float]] = {}
    for r in rows:
        by_isin.setdefault(r["isin"], {})[_bucket(r["time"])] = float(r["nav"])

    all_buckets = sorted({b for series in by_isin.values() for b in series})
    grid: list[dt.datetime] = []
    if all_buckets:
        t, end = all_buckets[0], all_buckets[-1]
        while t <= end:
            grid.append(t)
            t += dt.timedelta(minutes=BUCKET_MINUTES)

    out = []
    for f in funds:
        series = by_isin.get(f.isin)
        if not series:
            continue
        values: list[float | None] = []
        current = None
        for b in grid:
            current = series.get(b, current)
            values.append(current)
        observed = [v for v in values if v is not None]
        first, last = observed[0], observed[-1]
        prev_close = prev_closes.get(f.isin)
        base = prev_close or first
        out.append(
            GoldNavTrendFund(
                isin=f.isin,
                symbol=f.symbol,
                nav=values,
                first=first,
                last=last,
                prev_close=prev_close,
                change_pct=last / base - 1 if base else None,
            )
        )

    return GoldNavTrend(
        generated_at=now,
        source=NAV_SOURCE,
        bucket_minutes=BUCKET_MINUTES,
        times=[b.replace(tzinfo=TEHRAN) for b in grid],
        funds=out,
    )


async def build_gold_nav_trend(
    redis_client: redis.Redis,
    pg_pool: asyncpg.Pool,
    atlas_provider: AtlasProvider,
) -> GoldNavTrend:
    """Intraday NAV of every gold fund, for the website's NAV chart.

    Separate from /v1/gold/snapshot on purpose: the snapshot is polled by
    every page, this (~10 KB) only by the gold dashboard, and NAV moves
    about once a minute, so it is rebuilt far less often.
    """
    funds = await build_gold_funds_table(redis_client, pg_pool, atlas_provider)
    funds.sort(key=lambda f: f.market_cap or 0, reverse=True)
    isins = [f.isin for f in funds]
    rows = await pg_pool.fetch(_NAV_LAST_DAY_SQL, NAV_SOURCE, isins)
    prev_closes: dict[str, float] = {}
    if rows:
        day_start = rows[0]["time"].replace(hour=0, minute=0, second=0, microsecond=0)
        prev_rows = await pg_pool.fetch(_NAV_PREV_CLOSE_SQL, NAV_SOURCE, isins, day_start)
        prev_closes = {r["isin"]: float(r["nav"]) for r in prev_rows}
    return nav_trend_from_rows(funds, rows, dt.datetime.now(TEHRAN), prev_closes)
