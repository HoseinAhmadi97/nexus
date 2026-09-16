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


def _bucket(t: dt.datetime) -> dt.datetime:
    return t.replace(minute=t.minute - t.minute % BUCKET_MINUTES, second=0, microsecond=0)


def nav_trend_from_rows(funds: list[GoldFundRow], rows: list[Any], now: dt.datetime) -> GoldNavTrend:
    """Put every fund on one shared time grid.

    Each bucket holds the fund's last NAV inside it, carried forward
    through buckets with no update so lines don't break mid-session.
    Buckets before a fund's first NAV of the day stay None rather than
    being back-filled -- a line should start where the data starts.
    Funds keep the order given (largest market cap first).
    """
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
        out.append(
            GoldNavTrendFund(
                isin=f.isin,
                symbol=f.symbol,
                nav=values,
                first=first,
                last=last,
                change_pct=last / first - 1 if first else None,
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
    rows = await pg_pool.fetch(_NAV_LAST_DAY_SQL, NAV_SOURCE, [f.isin for f in funds])
    return nav_trend_from_rows(funds, rows, dt.datetime.now(TEHRAN))
