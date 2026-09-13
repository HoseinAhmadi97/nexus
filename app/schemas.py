from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel

from app.providers.base import PricePoint


class PricePointOut(BaseModel):
    isin: str
    source: str
    price: float | None
    updated_at: dt.datetime
    payload: dict[str, Any]

    @classmethod
    def from_point(cls, point: PricePoint) -> "PricePointOut":
        return cls(
            isin=point.isin,
            source=point.source,
            price=point.price,
            updated_at=point.updated_at,
            payload=point.payload,
        )


class GoldMarketRow(BaseModel):
    """One instrument in the gold/coin/dollar market snapshot."""

    symbol: str
    price: float | None
    source: str
    updated_at: dt.datetime
    #: Present only for a row assembled from more than one source
    #: (currently "dollar": wallex + tabdeal) -- the raw values behind
    #: the combined `price`, so nothing is hidden.
    components: list[dict[str, Any]] | None = None


class GoldFundRow(BaseModel):
    """One gold fund: live trade/order-book data joined with NAV from
    two independent providers and this month's portfolio weights."""

    isin: str
    symbol: str
    last_trade: float | None
    ask_price_1: float | None
    bid_price_1: float | None
    value: float | None
    volume: float | None
    #: NAV as reported by market_fetcher's live TSE snapshot.
    nav_live: float | None
    #: NAV from Atlas's two independent NAV providers -- compare them
    #: to gauge how much the two disagree (see the Atlas Grafana
    #: dashboard's NAV divergence panel for the same comparison).
    nav_tadbir: float | None
    nav_farabi: float | None
    #: (last_trade / nav_live) - 1. None if either input is missing.
    nominal_bubble: float | None
    #: This month's portfolio composition weights (sekke/shemsh/cash/...)
    #: from live.last_month_gold_compos, if this fund has a row there.
    weights: dict[str, float] | None
