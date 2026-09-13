from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from app.config import load_settings
from app.providers import AtlasProvider
from app.routers import prices


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)

    # One line per provider. A future non-Atlas source is a new
    # provider class (see app/providers/base.py) registered here --
    # routers never change.
    app.state.providers = {
        "atlas": AtlasProvider(pool),
    }

    yield

    await pool.close()


app = FastAPI(title="Nexus", lifespan=lifespan)
app.include_router(prices.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
