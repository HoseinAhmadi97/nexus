from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import GoldFundRow, GoldMarketRow
from app.services.gold_funds import build_gold_funds_table
from app.services.gold_market import build_gold_market_table

router = APIRouter(prefix="/v1/gold", tags=["gold"])


@router.get("/market", response_model=list[GoldMarketRow])
async def get_gold_market(request: Request) -> list[GoldMarketRow]:
    atlas_provider = request.app.state.providers["atlas"]
    return await build_gold_market_table(atlas_provider)


@router.get("/funds", response_model=list[GoldFundRow])
async def get_gold_funds(request: Request) -> list[GoldFundRow]:
    atlas_provider = request.app.state.providers["atlas"]
    return await build_gold_funds_table(
        request.app.state.redis, request.app.state.pg_pool, atlas_provider
    )
