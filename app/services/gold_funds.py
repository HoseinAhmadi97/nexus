from __future__ import annotations

import json
from typing import Any

import asyncpg
import redis.asyncio as redis

from app.providers.atlas_provider import AtlasProvider
from app.schemas import GoldFundRow

# Same 31 gold-fund TSE numeric ids the legacy gold_2.py pipeline used
# to filter market_fetcher's all_tickers_info (a DIFFERENT id system
# than Atlas's own gold_isins.py, which keys by IRTKxxxx isin, not this
# numeric internal TSE id). One duplicate in the original list, deduped
# here.
GOLD_FUND_TSE_IDS = frozenset(
    {
        "61805666737517582", "38544104313215500", "12390706505809150",
        "46700660505281786", "56987424987755487", "34144395039913458",
        "9089296888187061", "4626686276232042", "25559236668122210",
        "28255729477187163", "30582275818828857", "33144542989832366",
        "58514988269776425", "6362118829011821", "6237807001018762",
        "33254899395816171", "28374437855144739", "30895446582685604",
        "68376789401977331", "64795751499397128", "32469128621155736",
        "20244389840999638", "17248898258246807", "14035144070182412",
        "16817885126368964", "35389487611786089", "17244733069907210",
        "48968268685622891", "50072269736641214", "53514992320442853",
        "53633583359422860",
    }
)

_WEIGHTS_SQL = """
SELECT fund, sekke, shemsh, naghd, noghre, ayandeh
FROM live.last_month_gold_compos
"""


def _tickers_from_pandas_json(raw: bytes | str) -> list[dict[str, Any]]:
    """all_tickers_info is written by market_fetcher.py's
    `pandas.DataFrame.to_json()` (orient="columns" -- the pandas
    default): `{col: {row_index: value}}`. Reconstructed here by hand;
    Nexus doesn't otherwise depend on pandas."""
    data = json.loads(raw)
    if not data:
        return []
    row_ids = list(next(iter(data.values())).keys())
    return [{col: data[col].get(rid) for col in data} for rid in row_ids]


async def _read_weights(pg_pool: asyncpg.Pool) -> dict[str, dict[str, float]]:
    rows = await pg_pool.fetch(_WEIGHTS_SQL)
    return {
        r["fund"]: {
            "sekke_weight": r["sekke"],
            "shemsh_weight": r["shemsh"],
            "cash_weight": r["naghd"],
            "noghre_weight": r["noghre"],
            "ayandeh_weight": r["ayandeh"],
        }
        for r in rows
    }


async def build_gold_funds_table(
    redis_client: redis.Redis,
    pg_pool: asyncpg.Pool,
    atlas_provider: AtlasProvider,
) -> list[GoldFundRow]:
    """The gold funds snapshot: live trade/order-book data (from
    market_fetcher's all_tickers_info, the only source of that data --
    Atlas's NAV providers don't cover it) joined with NAV from Atlas's
    two independent NAV providers and this month's portfolio weights.

    Deliberately does not compute the legacy pipeline's intrinsic/
    sekke/shemsh bubble decomposition -- that cross-fund weighted math
    depends on unit assumptions (mesghal, dollar rate) not yet
    validated against the new datasources. nominal_bubble
    (last_trade/nav - 1) is included since it's unambiguous.
    """
    raw = await redis_client.get("all_tickers_info")
    tickers = _tickers_from_pandas_json(raw) if raw else []
    fund_tickers = [t for t in tickers if str(t.get("id")) in GOLD_FUND_TSE_IDS]

    tadbir_by_isin = {p.isin: p for p in await atlas_provider.list_latest(source="tadbir")}
    farabi_by_isin = {p.isin: p for p in await atlas_provider.list_latest(source="farabi")}
    weights_by_fund = await _read_weights(pg_pool)

    rows = []
    for t in fund_tickers:
        isin = t.get("isin")
        symbol = t.get("symbol")
        last_trade = t.get("last_trade")
        nav_live = t.get("nav")
        tadbir_point = tadbir_by_isin.get(isin)
        farabi_point = farabi_by_isin.get(isin)

        nominal_bubble = (
            (last_trade / nav_live) - 1 if last_trade and nav_live else None
        )

        rows.append(
            GoldFundRow(
                isin=isin,
                symbol=symbol,
                last_trade=last_trade,
                ask_price_1=t.get("ask_price_1"),
                bid_price_1=t.get("bid_price_1"),
                value=t.get("value"),
                volume=t.get("volume"),
                nav_live=nav_live,
                nav_tadbir=tadbir_point.price if tadbir_point else None,
                nav_farabi=farabi_point.price if farabi_point else None,
                nominal_bubble=nominal_bubble,
                weights=weights_by_fund.get(symbol),
            )
        )
    return rows
