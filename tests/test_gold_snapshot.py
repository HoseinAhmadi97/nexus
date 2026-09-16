import datetime as dt
import gzip
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.providers.atlas_provider import AtlasProvider
from app.providers.base import PricePoint
from app.routers import gold
from app.schemas import GoldFundRow, GoldSeriesPoint, GoldSnapshot, GoldSummary
from app.services import gold_market
from app.services.gold_funds import _trade_time
from app.services.gold_snapshot import TEHRAN, downsample, summarize
from app.snapshot_cache import SnapshotCache

NOW = dt.datetime(2026, 9, 16, 11, 0, tzinfo=TEHRAN)


def _fund(symbol, bubble, change_pct=None, trade_time="10:58:00", value=100.0, market_cap=1000.0):
    return GoldFundRow(
        isin="IR" + symbol, symbol=symbol, last_trade=1.0, ask_price_1=None, bid_price_1=None,
        value=value, volume=None, nav_live=None, nav_tadbir=None, nav_farabi=None,
        nominal_bubble=bubble, weights=None, change_pct=change_pct, trade_time=trade_time,
        market_cap=market_cap,
    )


# ── AtlasProvider.latest_for ────────────────────────────────────────

def _record(**kw):
    return dict(kw)


@pytest.mark.asyncio
async def test_latest_for_reads_redis_and_queries_postgres_only_for_missing_pairs():
    redis_client = MagicMock()
    redis_client.mget = AsyncMock(return_value=[
        json.dumps({"time": "2026-09-16T11:00:00+03:30", "isin": "geram18", "source": "estjt",
                    "price": 23491400.0, "payload": {}}),
        None,
    ])
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [_record(
        isin="GoldBar", source="ime", price="31060000", payload="{}",
        updated_at=dt.datetime(2026, 9, 16, 10, 0, tzinfo=TEHRAN),
    )]

    points = await AtlasProvider(redis_client, pg_pool).latest_for(
        [("estjt", "geram18"), ("ime", "GoldBar"), ("estjt", "geram18")]
    )

    redis_client.mget.assert_awaited_once_with(["atlas:raw:estjt:geram18", "atlas:raw:ime:GoldBar"])
    _, sources, isins = pg_pool.fetch.call_args.args
    assert (sources, isins) == (["ime"], ["GoldBar"])  # only the pair Redis lacked
    assert {(p.source, p.isin) for p in points} == {("estjt", "geram18"), ("ime", "GoldBar")}


@pytest.mark.asyncio
async def test_latest_for_skips_postgres_when_redis_has_everything():
    redis_client = MagicMock()
    redis_client.mget = AsyncMock(return_value=[json.dumps(
        {"time": "2026-09-16T11:00:00+03:30", "isin": "x", "source": "s", "price": 1.0, "payload": {}})])
    pg_pool = AsyncMock()

    await AtlasProvider(redis_client, pg_pool).latest_for([("s", "x")])

    pg_pool.fetch.assert_not_awaited()


# ── market day change and units ─────────────────────────────────────

@pytest.mark.asyncio
async def test_market_rows_carry_unit_and_day_change_from_previous_close():
    gold_market._prev_close_cache.clear()
    provider = AsyncMock()
    provider.latest_for.return_value = [
        PricePoint(isin="geram18", source="estjt", price=110.0, updated_at=NOW, payload={}),
        PricePoint(isin="GoldCoin", source="ime", price=50.0, updated_at=NOW, payload={}),
        PricePoint(isin="USDTTMN", source="wallex", price=200.0, updated_at=NOW, payload={}),
        PricePoint(isin="dollar", source="tabdeal", price=100.0, updated_at=NOW, payload={}),
    ]
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [
        _record(source="estjt", isin="geram18", price=100),
        _record(source="wallex", isin="USDTTMN", price=100),
        _record(source="tabdeal", isin="dollar", price=100),
    ]

    rows = {r.symbol: r for r in await gold_market.build_gold_market_table(provider, pg_pool)}

    assert rows["geram18"].unit == "IRT"
    assert rows["geram18"].change == 10.0
    assert rows["geram18"].change_pct == pytest.approx(0.10)
    assert rows["govahi_sekke"].unit == "IRR"
    assert rows["govahi_sekke"].change_pct is None  # no previous close
    assert rows["dollar"].price == 150.0
    assert rows["dollar"].change_pct == pytest.approx(0.5)
    gold_market._prev_close_cache.clear()


@pytest.mark.asyncio
async def test_dollar_change_is_none_when_a_component_has_no_previous_close():
    gold_market._prev_close_cache.clear()
    provider = AsyncMock()
    provider.latest_for.return_value = [
        PricePoint(isin="USDTTMN", source="wallex", price=200.0, updated_at=NOW, payload={}),
        PricePoint(isin="dollar", source="tabdeal", price=100.0, updated_at=NOW, payload={}),
    ]
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = [_record(source="wallex", isin="USDTTMN", price=100)]

    rows = {r.symbol: r for r in await gold_market.build_gold_market_table(provider, pg_pool)}

    assert rows["dollar"].change_pct is None
    gold_market._prev_close_cache.clear()


@pytest.mark.asyncio
async def test_previous_closes_are_queried_once_per_tehran_day():
    gold_market._prev_close_cache.clear()
    pg_pool = AsyncMock()
    pg_pool.fetch.return_value = []

    await gold_market.read_prev_closes(pg_pool)
    await gold_market.read_prev_closes(pg_pool)

    assert pg_pool.fetch.await_count == 1
    gold_market._prev_close_cache.clear()


# ── funds and summary ───────────────────────────────────────────────

def test_trade_time_formats_hhmmss_integers():
    assert _trade_time(143921) == "14:39:21"
    assert _trade_time(91005) == "09:10:05"
    assert _trade_time(None) is None


def test_summary_stats_and_market_open():
    funds = [
        _fund("a", -0.02, change_pct=0.01, trade_time="10:50:00"),
        _fund("b", 0.01, change_pct=0.03, trade_time="10:58:00"),
        _fund("c", None, trade_time=None, value=None, market_cap=None),
    ]

    summary = summarize(funds, NOW)

    assert summary.fund_count == 3
    assert summary.avg_bubble == pytest.approx(-0.005)
    assert summary.max_bubble == {"symbol": "b", "bubble": 0.01}
    assert summary.min_bubble == {"symbol": "a", "bubble": -0.02}
    assert summary.avg_change_pct == pytest.approx(0.02)
    assert summary.total_value == 200.0
    assert summary.last_trade_time == "10:58:00"
    assert summary.market_open is True


def test_market_is_closed_when_the_last_trade_is_stale():
    assert summarize([_fund("a", 0.0, trade_time="09:30:00")], NOW).market_open is False


def test_downsample_keeps_first_and_last_point():
    points = [GoldSeriesPoint(time=NOW, value=float(i)) for i in range(500)]

    thinned = downsample(points, limit=60)

    assert len(thinned) == 60
    assert thinned[0].value == 0.0
    assert thinned[-1].value == 499.0


# ── cache and endpoint ──────────────────────────────────────────────

def _snapshot(avg_bubble=-0.01):
    return GoldSnapshot(
        generated_at=NOW,
        summary=GoldSummary(
            fund_count=0, avg_bubble=avg_bubble, max_bubble=None, min_bubble=None,
            avg_change_pct=None, total_value=0, total_market_cap=0,
            last_trade_time=None, market_open=False,
        ),
        market=[], funds=[], series={},
    )


@pytest.mark.asyncio
async def test_cache_keeps_the_etag_when_the_document_is_unchanged():
    cache = SnapshotCache(AsyncMock(return_value=_snapshot()), interval=10)
    await cache.refresh()
    first = cache.etag
    await cache.refresh()
    assert cache.etag == first


@pytest.mark.asyncio
async def test_cache_serves_the_previous_document_when_a_build_fails():
    build = AsyncMock(side_effect=[_snapshot(), RuntimeError("redis down")])
    cache = SnapshotCache(build, interval=10)
    await cache.refresh()
    body = cache.body
    with pytest.raises(RuntimeError):
        await cache.refresh()
    assert cache.body == body


@pytest.fixture
def snapshot_client():
    test_app = FastAPI()
    test_app.include_router(gold.router)
    test_app.state.gold_snapshot = SnapshotCache(AsyncMock(return_value=_snapshot()), interval=10)
    with TestClient(test_app) as c:
        yield c


def test_snapshot_endpoint_serves_json_with_etag(snapshot_client):
    resp = snapshot_client.get("/v1/gold/snapshot", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert resp.headers["etag"]
    assert resp.headers["cache-control"] == "public, max-age=5"
    assert resp.json()["summary"]["avg_bubble"] == -0.01


def test_snapshot_endpoint_returns_304_for_a_matching_etag(snapshot_client):
    etag = snapshot_client.get("/v1/gold/snapshot").headers["etag"]
    resp = snapshot_client.get("/v1/gold/snapshot", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.content == b""


def test_snapshot_endpoint_serves_pre_gzipped_body(snapshot_client):
    cache = snapshot_client.app.state.gold_snapshot
    resp = snapshot_client.get("/v1/gold/snapshot", headers={"Accept-Encoding": "gzip"})
    assert resp.headers["content-encoding"] == "gzip"
    assert gzip.decompress(cache.gzip_body) == cache.body
