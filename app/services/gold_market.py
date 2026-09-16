from __future__ import annotations

import datetime as dt

import asyncpg

from app.providers.atlas_provider import AtlasProvider
from app.schemas import GoldMarketRow
from app.services.gold_intrinsic import apply_certificate_intrinsics

# name -> (source, isin) in Atlas. Confirmed with the user which source
# is authoritative per instrument (2026-09-13, replacing a legacy
# pipeline's ClickHouse "mabna" feed that no longer exists on this
# server): golds from estjt, dollar averaged across wallex + tabdeal
# (two different instruments -- USDT/Toman and cash Toman rate -- used
# as a proxy pair, not an exact match), deposit certificates from IME
# (govahi_sekke/govahi_shemsh -- exchange-traded gold/coin certificates,
# which estjt/tabdeal don't cover but IME's GoldCoin/GoldBar contracts
# are exactly).
#
# Added 2026-09-16 for the website's gold pages, confirmed with the user
# the same day: the smaller coins from estjt (same source as "sekee"),
# and "mesghal" from tabdeal's gold_melt ("طلای آبشده", the melted-gold
# price per mesghal -- it tracks geram18 x 4.3318 to within 0.3%). No
# estjt equivalent exists for mesghal.
SYMBOL_SOURCES: dict[str, tuple[str, str]] = {
    "ons": ("estjt", "ons_tala"),
    "geram18": ("estjt", "geram18"),
    "geram24": ("estjt", "geram24"),
    "sekee": ("estjt", "sekee_new"),
    "sekee_bahar": ("estjt", "sekee_old"),
    "nim": ("estjt", "nim"),
    "rob": ("estjt", "rob"),
    "gerami": ("estjt", "gerami"),
    "mesghal": ("tabdeal", "gold_melt"),
    "govahi_sekke": ("ime", "GoldCoin"),
    "govahi_shemsh": ("ime", "GoldBar"),
}
DOLLAR_SOURCES: list[tuple[str, str]] = [("wallex", "USDTTMN"), ("tabdeal", "dollar")]

# Native unit per Atlas source. Prices are reported as the source gives
# them -- never converted here -- with this as `unit`, so a consumer can
# convert explicitly instead of guessing. IME GoldBar is IRR per 100 mg
# (its ContractSize is 10); that is still IRR, just not per gram.
SOURCE_UNITS: dict[str, str] = {
    "estjt": "IRT",
    "tabdeal": "IRT",
    "wallex": "IRT",
    "ime": "IRR",
}
_USD_SYMBOLS = frozenset({"ons"})

LABELS: dict[str, str] = {
    "ons": "انس جهانی طلا",
    "geram18": "طلای ۱۸ عیار",
    "geram24": "طلای ۲۴ عیار",
    "sekee": "سکه امامی",
    "sekee_bahar": "سکه بهار آزادی",
    "nim": "نیم سکه",
    "rob": "ربع سکه",
    "gerami": "سکه گرمی",
    "mesghal": "مظنه آبشده (مثقال)",
    "govahi_sekke": "گواهی سکه",
    "govahi_shemsh": "گواهی شمش",
    "dollar": "دلار",
}

# Last tick before Tehran midnight, per (source, isin): the previous
# day's close for day-change. raw_ticks.time is naive Tehran local time
# (the same convention AtlasProvider's queries rely on). Bounded to 10
# days back so a weekend or holiday still finds a close without scanning
# the whole table.
_PREV_CLOSE_SQL = """
SELECT DISTINCT ON (source, isin) source, isin, price
FROM atlas.raw_ticks
WHERE time < date_trunc('day', now() AT TIME ZONE 'Asia/Tehran')
  AND time >= date_trunc('day', now() AT TIME ZONE 'Asia/Tehran') - interval '10 days'
  AND source = ANY($1::text[])
ORDER BY source, isin, time DESC
"""

# A previous close only changes when the Tehran date does, and the query
# costs ~0.3 s -- so it runs once per day per process, not per build.
_prev_close_cache: dict[str, dict[tuple[str, str], float]] = {}


def _tehran_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3, minutes=30)).date().isoformat()


async def read_prev_closes(pg_pool: asyncpg.Pool) -> dict[tuple[str, str], float]:
    today = _tehran_today()
    cached = _prev_close_cache.get(today)
    if cached is not None:
        return cached
    sources = sorted({s for s, _ in SYMBOL_SOURCES.values()} | {s for s, _ in DOLLAR_SOURCES})
    rows = await pg_pool.fetch(_PREV_CLOSE_SQL, sources)
    closes = {
        (r["source"], r["isin"]): float(r["price"]) for r in rows if r["price"] is not None
    }
    _prev_close_cache.clear()
    _prev_close_cache[today] = closes
    return closes


def _change(price: float | None, prev: float | None) -> tuple[float | None, float | None]:
    if price is None or not prev:
        return None, None
    return price - prev, price / prev - 1


async def build_gold_market_table(
    atlas_provider: AtlasProvider,
    pg_pool: asyncpg.Pool | None = None,
) -> list[GoldMarketRow]:
    """The gold/coin/dollar market snapshot -- one row per instrument,
    each sourced from the specific Atlas (source, isin) confirmed above.

    With `pg_pool`, rows also carry the previous close and day change.
    """
    all_points = await atlas_provider.latest_for(list(SYMBOL_SOURCES.values()) + DOLLAR_SOURCES)
    by_source_isin = {(p.source, p.isin): p for p in all_points}
    prev_closes = await read_prev_closes(pg_pool) if pg_pool is not None else {}

    rows = []
    for symbol, key in SYMBOL_SOURCES.items():
        point = by_source_isin.get(key)
        if point is None:
            continue
        source, _isin = key
        prev = prev_closes.get(key)
        change, change_pct = _change(point.price, prev)
        rows.append(
            GoldMarketRow(
                symbol=symbol,
                label=LABELS[symbol],
                price=point.price,
                unit="USD" if symbol in _USD_SYMBOLS else SOURCE_UNITS[source],
                source=source,
                updated_at=point.updated_at,
                prev_close=prev,
                change=change,
                change_pct=change_pct,
            )
        )

    dollar_points = [
        (k, p) for k, p in ((k, by_source_isin.get(k)) for k in DOLLAR_SOURCES) if p is not None
    ]
    priced = [(k, p) for k, p in dollar_points if p.price is not None]
    if priced:
        price = sum(p.price for _, p in priced) / len(priced)
        prevs = [prev_closes[k] for k, _ in priced if k in prev_closes]
        # Only average the previous closes when every priced component has
        # one -- otherwise the change would compare different baskets.
        prev = sum(prevs) / len(prevs) if len(prevs) == len(priced) else None
        change, change_pct = _change(price, prev)
        rows.append(
            GoldMarketRow(
                symbol="dollar",
                label=LABELS["dollar"],
                price=price,
                unit="IRT",
                source="avg(wallex,tabdeal)",
                updated_at=max(p.updated_at for _, p in dollar_points),
                components=[{"source": p.source, "price": p.price} for _, p in dollar_points],
                prev_close=prev,
                change=change,
                change_pct=change_pct,
            )
        )
    apply_certificate_intrinsics(rows)
    return rows
