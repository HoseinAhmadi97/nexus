import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.providers.base import PricePoint, PriceProvider
from app.routers import prices


class FakeProvider(PriceProvider):
    """In-memory stand-in for a real provider -- lets router tests run
    with no database at all."""

    name = "fake"

    def __init__(self, points: list[PricePoint]) -> None:
        self.points = points

    async def latest(self, isin: str) -> list[PricePoint]:
        return [p for p in self.points if p.isin == isin]

    async def list_latest(self, source: str | None = None) -> list[PricePoint]:
        if source is None:
            return list(self.points)
        return [p for p in self.points if p.source == source]


SAMPLE_POINTS = [
    PricePoint(
        isin="geram18",
        source="estjt",
        price=23600000.0,
        updated_at=dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc),
        payload={"name": "test"},
    ),
    PricePoint(
        isin="geram18",
        source="tabdeal",
        price=23590000.0,
        updated_at=dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc),
        payload={"name": "test"},
    ),
    PricePoint(
        isin="USDTTMN",
        source="wallex",
        price=233000.0,
        updated_at=dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc),
        payload={},
    ),
]


@pytest.fixture
def client():
    """A minimal test app mounting just the prices router, with a
    FakeProvider in app.state -- deliberately not the real `app.main`
    instance, whose lifespan creates a real asyncpg pool against a live
    Postgres. Router behavior is what's under test here, not startup
    wiring."""
    test_app = FastAPI()
    test_app.include_router(prices.router)
    test_app.state.providers = {"fake": FakeProvider(SAMPLE_POINTS)}
    with TestClient(test_app) as c:
        yield c
