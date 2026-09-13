# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

The API layer for the server -- FastAPI, built to serve APIs for
different parts of the organization, not scoped to one domain. Atlas
(the raw ingestion project, `~/atlas` on the server) is the first
backend it reads from, via `AtlasProvider` reading Atlas's Redis
keyspace and `atlas.raw_ticks` table directly -- not by importing
Atlas's Python package. That is the only coupling between the two
projects, and it is deliberately thin: Nexus depends on Atlas's data
contracts (a keyspace convention and a table schema), not its code.

## Invariants

1. **Routers only ever talk to `request.app.state.providers`, never to
   a database or Redis directly.** A provider is a `PriceProvider`
   (`app/providers/base.py`) with `latest(isin)` and
   `list_latest(source=None)`. Adding a datasource that isn't Atlas is
   a new provider class plus one registration line in
   `app/main.py`'s `lifespan()` -- if a change to add a source touches
   `app/routers/prices.py` or `app/schemas.py`, stop and reconsider.

2. **`/v1/prices/{isin}` returns every source's point for that isin,
   never one picked winner.** Atlas already has isins multiple sources
   report (`geram18` from both `estjt` and `tabdeal`); Nexus is not the
   layer that decides which is authoritative. Don't add a "best price"
   / dedup step here -- that is a downstream decision this project
   hasn't been asked to make yet.

3. **`AtlasProvider` reads Redis first; Postgres is a fallback for
   `(isin, source)` pairs Redis didn't have, not a second primary
   path.** Atlas's `RedisSink` maintains a no-TTL, continuously-
   overwritten latest-value cache at `atlas:raw:{source}:{isin}`
   specifically so a current-value read never has to touch Postgres --
   that's the point of asking for this, and it's the faster path.
   `latest()`/`list_latest()` scan Redis, then query
   `atlas.raw_ticks` and only append rows whose `(isin, source)` wasn't
   already found in Redis -- never both unconditionally, which would
   return the same source twice.

4. **Endpoints are `async def` and use `asyncpg`/`redis.asyncio` (not
   sync drivers) end to end.** FastAPI's concurrency benefit only
   holds if the whole chain is non-blocking -- a sync DB call (e.g.
   psycopg2, which Atlas uses because it doesn't need this) inside an
   `async def` endpoint would block the entire event loop, not just
   that one request. `AtlasProvider` uses a shared `asyncpg.Pool` and
   a shared `redis.asyncio.Redis` client (`app/main.py`'s
   `lifespan()`), not per-request connections.

5. **`asyncpg` returns `numeric` columns as values that must be cast to
   `float` explicitly, and `jsonb` columns as `str`, not `dict`.**
   `_point_from_pg_row()` does both conversions -- found by actually
   running a query against live `atlas.raw_ticks` before trusting the
   code, not assumed. Don't remove either cast.

6. **This repo does not modify or import from `~/atlas`.** They are
   separate GitHub repos and separate deployed processes on the same
   server; the only relationship is `AtlasProvider` knowing Atlas's
   Redis key format and `atlas.raw_ticks`'s columns. If either
   changes, `AtlasProvider` needs updating -- but that's a
   coordination point between the two projects' CLAUDE.md files, not a
   code dependency.

## Conventions

- `from __future__ import annotations` throughout, matching Atlas's
  Python-3.10 target.
- Tests never need a real Postgres or Redis:
  `tests/test_prices_router.py` uses a `FakeProvider`
  (`tests/conftest.py`) against a minimal test app (not the real
  `app.main.app`, whose `lifespan()` creates real connections);
  `tests/test_atlas_provider.py` mocks `asyncpg.Pool` and the Redis
  client directly.

## Verifying changes

```bash
python -m py_compile app/*.py app/**/*.py
pytest
```

Running the server for real needs a reachable Postgres
(`NEXUS_PG_DSN`) and Redis (`NEXUS_REDIS_URL`) -- see README
"Prerequisites".
