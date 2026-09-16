from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.schemas import GoldFundRow, GoldMarketRow, GoldNavTrend, GoldSnapshot
from app.services.gold_funds import build_gold_funds_table
from app.services.gold_market import build_gold_market_table

router = APIRouter(prefix="/v1/gold", tags=["gold"])


@router.get("/market", response_model=list[GoldMarketRow])
async def get_gold_market(request: Request) -> list[GoldMarketRow]:
    atlas_provider = request.app.state.providers["atlas"]
    return await build_gold_market_table(atlas_provider, request.app.state.pg_pool)


@router.get("/funds", response_model=list[GoldFundRow])
async def get_gold_funds(request: Request) -> list[GoldFundRow]:
    atlas_provider = request.app.state.providers["atlas"]
    return await build_gold_funds_table(
        request.app.state.redis, request.app.state.pg_pool, atlas_provider
    )


@router.get("/snapshot", response_model=GoldSnapshot)
async def get_gold_snapshot(request: Request) -> Response:
    """Market + funds + summary + intraday series in one document, for
    the website. Served from app.state.gold_snapshot (rebuilt on a timer
    in the background), never built per request -- see SnapshotCache."""
    return await _serve_cached(request, request.app.state.gold_snapshot)


@router.get("/nav-trend", response_model=GoldNavTrend)
async def get_gold_nav_trend(request: Request) -> Response:
    """Every gold fund's intraday NAV on one time grid, for the website's
    NAV chart. Served from app.state.gold_nav_trend, rebuilt on a timer."""
    return await _serve_cached(request, request.app.state.gold_nav_trend)


async def _serve_cached(request: Request, cache) -> Response:
    await cache.ensure()

    # Clients may reuse a response for part of one rebuild interval; the
    # ETag makes every poll after that a cheap revalidation.
    headers = {
        "ETag": cache.etag,
        "Cache-Control": "public, max-age=%d" % max(1, int(cache.interval // 2)),
        "Vary": "Accept-Encoding",
    }
    if request.headers.get("if-none-match") == cache.etag:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(cache.gzip_body, media_type="application/json", headers=headers)
    return Response(cache.body, media_type="application/json", headers=headers)
