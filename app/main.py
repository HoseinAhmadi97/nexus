from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI

from app.config import load_settings
from app.providers import AtlasProvider
from app.routers import prices


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    redis_client = redis.from_url(settings.redis_url)
    pg_pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)

    # One line per provider. A future non-Atlas source is a new
    # provider class (see app/providers/base.py) registered here --
    # routers never change.
    app.state.providers = {
        "atlas": AtlasProvider(redis_client, pg_pool),
    }

    yield

    await redis_client.aclose()
    await pg_pool.close()


app = FastAPI(title="Nexus", lifespan=lifespan)
app.include_router(prices.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
