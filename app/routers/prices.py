from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.schemas import PricePointOut

router = APIRouter(prefix="/v1/prices", tags=["prices"])


@router.get("", response_model=list[PricePointOut])
async def list_prices(request: Request, source: str | None = None) -> list[PricePointOut]:
    """Latest point per (isin, source) across every registered provider."""
    points = []
    for provider in request.app.state.providers.values():
        points.extend(await provider.list_latest(source=source))
    return [PricePointOut.from_point(p) for p in points]


@router.get("/{isin}", response_model=list[PricePointOut])
async def get_price(request: Request, isin: str) -> list[PricePointOut]:
    """Every provider's latest point(s) for one isin.

    Returns one entry per source that reports this isin -- if more
    than one does (e.g. atlas's "geram18" from both estjt and
    tabdeal), Nexus hands back all of them rather than silently
    picking a winner; deciding which source is authoritative for a
    given isin is a downstream/business decision, not this layer's.
    """
    points = []
    for provider in request.app.state.providers.values():
        points.extend(await provider.latest(isin))
    if not points:
        raise HTTPException(status_code=404, detail=f"no price data for isin '{isin}'")
    return [PricePointOut.from_point(p) for p in points]
