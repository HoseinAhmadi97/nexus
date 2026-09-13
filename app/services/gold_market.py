from __future__ import annotations

from app.providers.atlas_provider import AtlasProvider
from app.schemas import GoldMarketRow

# name -> (source, isin) in Atlas. Confirmed with the user which source
# is authoritative per instrument (2026-09-13, replacing a legacy
# pipeline's ClickHouse "mabna" feed that no longer exists on this
# server): golds from estjt, dollar averaged across wallex + tabdeal
# (two different instruments -- USDT/Toman and cash Toman rate -- used
# as a proxy pair, not an exact match), deposit certificates from IME
# (govahi_sekke/govahi_shemsh -- exchange-traded gold/coin certificates,
# which estjt/tabdeal don't cover but IME's GoldCoin/GoldBar contracts
# are exactly). "mesghal" (Gold Mesghal) has no equivalent in any
# current Atlas datasource and is deliberately left out, not guessed at.
SYMBOL_SOURCES: dict[str, tuple[str, str]] = {
    "ons": ("estjt", "ons_tala"),
    "geram18": ("estjt", "geram18"),
    "geram24": ("estjt", "geram24"),
    "sekee": ("estjt", "sekee_new"),
    "govahi_sekke": ("ime", "GoldCoin"),
    "govahi_shemsh": ("ime", "GoldBar"),
}
DOLLAR_SOURCES: list[tuple[str, str]] = [("wallex", "USDTTMN"), ("tabdeal", "dollar")]


async def build_gold_market_table(atlas_provider: AtlasProvider) -> list[GoldMarketRow]:
    """The gold/coin/dollar market snapshot -- one row per instrument,
    each sourced from the specific Atlas (source, isin) confirmed above."""
    all_points = await atlas_provider.list_latest()
    by_source_isin = {(p.source, p.isin): p for p in all_points}

    rows = []
    for symbol, key in SYMBOL_SOURCES.items():
        point = by_source_isin.get(key)
        if point is None:
            continue
        source, _isin = key
        rows.append(
            GoldMarketRow(
                symbol=symbol,
                price=point.price,
                source=source,
                updated_at=point.updated_at,
            )
        )

    dollar_points = [p for p in (by_source_isin.get(k) for k in DOLLAR_SOURCES) if p is not None]
    prices = [p.price for p in dollar_points if p.price is not None]
    if prices:
        rows.append(
            GoldMarketRow(
                symbol="dollar",
                price=sum(prices) / len(prices),
                source="avg(wallex,tabdeal)",
                updated_at=max(p.updated_at for p in dollar_points),
                components=[{"source": p.source, "price": p.price} for p in dollar_points],
            )
        )
    return rows
