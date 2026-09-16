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
    #: Persian display name.
    label: str | None = None
    #: The native unit of `price`, never converted: "IRR", "IRT" (toman)
    #: or "USD". See SOURCE_UNITS in gold_market.py.
    unit: str | None = None
    #: Last price before Tehran midnight, and the change against it.
    #: None when no previous close exists in the last 10 days.
    prev_close: float | None = None
    change: float | None = None
    #: A fraction, not a percentage: 0.011 means +1.1%.
    change_pct: float | None = None


class GoldFundRow(BaseModel):
    """One gold fund: live trade/order-book data joined with NAV from
    two independent providers and this month's portfolio weights."""

    isin: str
    symbol: str
    #: Full fund name from TSE.
    name: str | None = None
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
    #: (last_trade / nav) - 1. None if either input is missing.
    nominal_bubble: float | None
    #: The NAV of record: farabi's. What consumers should show and what
    #: nominal_bubble uses; nav_live and nav_tadbir are for comparison.
    nav: float | None = None
    #: This month's portfolio composition weights (sekke/shemsh/cash/...)
    #: from live.last_month_gold_compos, if this fund has a row there.
    weights: dict[str, float] | None
    #: TSE closing (weighted-average) price and previous-day reference
    #: price. All fund prices are IRR.
    close_price: float | None = None
    yesterday_price: float | None = None
    #: last_trade vs yesterday_price. change_pct is a fraction.
    change: float | None = None
    change_pct: float | None = None
    #: Time of the last trade, "HH:MM:SS" Tehran time. TSE gives no date.
    trade_time: str | None = None
    market_cap: float | None = None


class GoldSeriesPoint(BaseModel):
    time: dt.datetime
    value: float


class GoldSeries(BaseModel):
    """One intraday line: the most recent day that has data."""

    key: str
    label: str
    unit: str
    source: str
    points: list[GoldSeriesPoint]


class GoldSummary(BaseModel):
    fund_count: int
    #: Simple mean of nominal_bubble across funds that have one.
    avg_bubble: float | None
    #: {"symbol": ..., "bubble": ...} for the highest / lowest bubble.
    max_bubble: dict[str, Any] | None
    min_bubble: dict[str, Any] | None
    #: Mean day change of the funds -- a stand-in until a real gold fund
    #: index exists (none of the datasources has one).
    avg_change_pct: float | None
    #: Sums across the funds, IRR.
    total_value: float
    total_market_cap: float
    #: Latest fund trade time ("HH:MM:SS").
    last_trade_time: str | None
    #: Whether the gold fund market is in session now, by schedule:
    #: Saturday-Wednesday, 12:00-18:00 Tehran. Official holidays are not
    #: known to Nexus.
    market_open: bool


class GoldNavTrendFund(BaseModel):
    isin: str
    symbol: str
    #: NAV (IRR) per bucket of GoldNavTrend.times; None before the fund's
    #: first NAV of the day.
    nav: list[float | None]
    first: float | None
    last: float | None
    #: Last NAV before the trend's day (yesterday's close), if any in the
    #: previous 10 days.
    prev_close: float | None = None
    #: last / prev_close - 1 (falls back to last / first - 1), a fraction.
    change_pct: float | None


class GoldNavTrend(BaseModel):
    """Every gold fund's intraday NAV on one shared time grid (the most
    recent day with data), largest fund by market cap first."""

    generated_at: dt.datetime
    source: str
    bucket_minutes: int
    times: list[dt.datetime]
    funds: list[GoldNavTrendFund]


class GoldSnapshot(BaseModel):
    """Everything the website's gold pages show, in one document."""

    generated_at: dt.datetime
    summary: GoldSummary
    market: list[GoldMarketRow]
    funds: list[GoldFundRow]
    series: dict[str, GoldSeries]
