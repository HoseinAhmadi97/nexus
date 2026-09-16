import datetime as dt
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import gold
from app.schemas import GoldFundRow
from app.services.gold_nav_trend import nav_trend_from_rows
from app.services.gold_snapshot import TEHRAN
from app.snapshot_cache import SnapshotCache

NOW = dt.datetime(2026, 9, 16, 12, 30, tzinfo=TEHRAN)


def _fund(isin, symbol):
    return GoldFundRow(
        isin=isin, symbol=symbol, last_trade=None, ask_price_1=None, bid_price_1=None,
        value=None, volume=None, nav_live=None, nav_tadbir=None, nav_farabi=None,
        nominal_bubble=None, weights=None,
    )


def _row(isin, hh, mm, nav):
    return {"isin": isin, "time": dt.datetime(2026, 9, 16, hh, mm), "nav": nav}


def test_funds_share_one_grid_and_carry_nav_forward():
    rows = [
        _row("A", 12, 0, 100), _row("A", 12, 3, 101),   # same 12:00 bucket: last wins
        _row("A", 12, 12, 103),                           # 12:05 has no update
        _row("B", 12, 5, 50), _row("B", 12, 10, 49),
    ]

    trend = nav_trend_from_rows([_fund("A", "a"), _fund("B", "b")], rows, NOW)

    assert [t.strftime("%H:%M") for t in trend.times] == ["12:00", "12:05", "12:10"]
    a, b = trend.funds
    assert a.nav == [101, 101, 103]
    assert b.nav == [None, 50, 49]           # no back-fill before its first NAV
    assert a.change_pct == pytest.approx(103 / 101 - 1)
    assert (b.first, b.last) == (50, 49)
    assert trend.times[0].tzinfo == TEHRAN


def test_change_is_measured_from_the_previous_close_when_there_is_one():
    rows = [_row("A", 12, 0, 100), _row("A", 12, 5, 110), _row("B", 12, 0, 50), _row("B", 12, 5, 55)]

    trend = nav_trend_from_rows([_fund("A", "a"), _fund("B", "b")], rows, NOW, {"A": 88})

    a, b = trend.funds
    assert a.prev_close == 88
    assert a.change_pct == pytest.approx(110 / 88 - 1)   # vs yesterday, not vs 100
    assert b.prev_close is None
    assert b.change_pct == pytest.approx(55 / 50 - 1)    # no close: first NAV of the day


def test_funds_without_nav_rows_are_left_out_and_order_is_kept():
    rows = [_row("B", 12, 0, 50), _row("A", 12, 0, 100)]

    trend = nav_trend_from_rows(
        [_fund("B", "b"), _fund("X", "x"), _fund("A", "a")], rows, NOW
    )

    assert [f.symbol for f in trend.funds] == ["b", "a"]


def test_no_rows_gives_an_empty_trend():
    trend = nav_trend_from_rows([_fund("A", "a")], [], NOW)
    assert trend.times == [] and trend.funds == []


def test_nav_trend_endpoint_is_served_from_its_cache():
    app = FastAPI()
    app.include_router(gold.router)
    doc = nav_trend_from_rows([_fund("A", "a")], [_row("A", 12, 0, 100)], NOW)
    app.state.gold_nav_trend = SnapshotCache(AsyncMock(return_value=doc), interval=60)

    with TestClient(app) as client:
        resp = client.get("/v1/gold/nav-trend")
        again = client.get("/v1/gold/nav-trend", headers={"If-None-Match": resp.headers["etag"]})

    assert resp.status_code == 200
    assert resp.json()["funds"][0]["symbol"] == "a"
    assert resp.headers["cache-control"] == "public, max-age=30"
    assert again.status_code == 304
