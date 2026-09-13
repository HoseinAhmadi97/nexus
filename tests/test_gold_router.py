from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import gold


@pytest.fixture
def gold_client():
    test_app = FastAPI()
    test_app.include_router(gold.router)
    test_app.state.providers = {"atlas": object()}  # opaque; services are mocked below
    test_app.state.redis = object()
    test_app.state.pg_pool = object()
    with TestClient(test_app) as c:
        yield c


def test_market_endpoint_returns_service_output(gold_client):
    with patch(
        "app.routers.gold.build_gold_market_table",
        new_callable=AsyncMock,
        return_value=[
            {
                "symbol": "ons",
                "price": 4348.0,
                "source": "estjt",
                "updated_at": "2026-09-13T12:00:00Z",
                "components": None,
            }
        ],
    ):
        resp = gold_client.get("/v1/gold/market")

    assert resp.status_code == 200
    assert resp.json()[0]["symbol"] == "ons"


def test_funds_endpoint_passes_redis_and_pg_pool_through(gold_client):
    with patch(
        "app.routers.gold.build_gold_funds_table", new_callable=AsyncMock, return_value=[]
    ) as mock_build:
        resp = gold_client.get("/v1/gold/funds")

    assert resp.status_code == 200
    assert resp.json() == []
    args, _ = mock_build.call_args
    redis_arg, pg_pool_arg, atlas_arg = args
    assert redis_arg is gold_client.app.state.redis
    assert pg_pool_arg is gold_client.app.state.pg_pool
    assert atlas_arg is gold_client.app.state.providers["atlas"]
