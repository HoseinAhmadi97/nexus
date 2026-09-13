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
