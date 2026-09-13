# Architecture

```
request
   |
   v
app/routers/prices.py  -- iterates request.app.state.providers
   |
   v
PriceProvider (app/providers/base.py)  <-- the only contract
   |
   +-- AtlasProvider  -- reads atlas.raw_ticks over SQL
   |                     (Atlas's raw ingestion layer, a separate
   |                      project/repo/process)
   |
   `-- (future) SomeOtherProvider  -- a different backend entirely,
                                      registered the same way
```

## Why a provider abstraction, not a direct DB dependency

Nexus was asked for as "the layer that builds all the APIs different
parts of the organization need" -- not "the API for Atlas specifically."
Routers and response schemas are written against `PriceProvider`, an
interface with two methods (`latest`, `list_latest`), so:

- adding a datasource that has nothing to do with Atlas doesn't touch
  `app/routers/` or `app/schemas.py` at all -- it's a new class in
  `app/providers/` plus one line in `app/main.py`'s `lifespan()`
- `AtlasProvider` itself only knows `atlas.raw_ticks`'s columns, not
  Atlas's Python code -- the two projects can be deployed, tested, and
  changed independently, coordinating only through that table's schema

## Why `/v1/prices/{isin}` doesn't pick a winner

Atlas already has isins more than one source reports (`geram18` from
both `estjt` and `tabdeal`, discovered while building the Atlas Grafana
dashboard -- the two sources' prices don't always agree exactly).
Nexus returns every source's point for a given isin rather than
silently choosing one: which source should be authoritative (or how to
reconcile disagreement) is a business decision nobody has made yet.
Making that call implicitly, inside a generic API layer, would bury a
real decision as an implementation detail. When that decision is made,
it's a new endpoint or a new provider that composes over the raw ones
-- not a change to what `/v1/prices/{isin}` already returns.

## Why `asyncpg`, not `psycopg2`

Atlas uses `psycopg2` (synchronous) because its ingestion loop doesn't
need concurrency within one process -- it fetches, then writes, one
datasource at a time. Nexus is different: it's meant to serve many
concurrent HTTP requests, and FastAPI's whole concurrency model
depends on the request-handling chain staying non-blocking end to end.
A synchronous DB call inside an `async def` endpoint would block the
entire event loop for every other in-flight request, not just the one
making that call -- so `AtlasProvider` uses `asyncpg` and a shared
connection pool (`asyncpg.create_pool`, set up once in `app/main.py`'s
`lifespan()`), not a single connection reused across requests.
