# Nexus

The API layer for the server: builds and serves the APIs different
parts of the organization need. Not tied to any one upstream data
source -- Atlas (the raw price/time-series ingestion layer) is the
first backend it reads from, not the only one it's meant to.

## Structure

```
app/
|-- main.py               # FastAPI app, lifespan wires up providers
|-- config.py              # Settings: DSNs, host, port from env/.env
|-- schemas.py              # Pydantic response models
|-- providers/
|   |-- base.py            # PriceProvider ABC + PricePoint -- the only contract
|   `-- atlas_provider.py  # reads Atlas: Redis first, Postgres as fallback
`-- routers/
    `-- prices.py           # GET /v1/prices, /v1/prices/{isin}

deploy/nexus.service        # systemd unit
tests/
docs/architecture.md
```

## Why a provider abstraction

Routers never talk to a database (or Redis) directly -- they ask
`request.app.state.providers`, a dict of `PriceProvider` instances, for
price points. `AtlasProvider` is the first one (Nexus depends on
Atlas's *data contracts* -- its Redis keyspace and its table's columns
-- not on Atlas's Python package; they're separate deployable
projects). A second, unrelated data source in the future is a new file
in `app/providers/` plus one line registering it in `app/main.py`'s
`lifespan()` -- routers and schemas never need to change. See
`app/providers/base.py`.

## AtlasProvider: Redis first, Postgres as fallback

Atlas's `RedisSink` already maintains a no-TTL, continuously-overwritten
"latest value" snapshot at `atlas:raw:{source}:{isin}` for exactly this
purpose -- a fast current-value read that never has to touch Postgres.
`AtlasProvider` reads Redis first; `atlas.raw_ticks` is only consulted
for a `(isin, source)` pair Redis didn't have (e.g. Redis was flushed,
or that source has never run since). Under normal operation this
fallback rarely fires -- Redis keys never expire, so "missing from
Redis" usually means "missing everywhere," not "Postgres has fresher
data." See `docs/architecture.md` for the merge logic.

## Adding a new provider

1. Subclass `PriceProvider` (`app/providers/base.py`): implement
   `latest(isin)` and `list_latest(source=None)`, both returning
   `list[PricePoint]`.
2. Register it in `app/main.py`'s `lifespan()`:
   `app.state.providers["my_source"] = MyProvider(...)`.
3. That's it -- `/v1/prices` and `/v1/prices/{isin}` automatically
   include it, since both routes iterate every registered provider.

## API

- `GET /v1/prices?source=<optional>` -- latest point per (isin,
  source) across every registered provider, optionally filtered to one
  source.
- `GET /v1/prices/{isin}` -- every source's latest point for one isin.
  If more than one source reports it (e.g. atlas's `geram18` from both
  `estjt` and `tabdeal`), **all** of them come back -- Nexus doesn't
  silently pick a winner. Deciding which source is authoritative for a
  given isin is a downstream/business decision, not this layer's, at
  least not yet.
- `GET /health` -- liveness check.
- `GET /docs` -- FastAPI's interactive Swagger UI (auto-generated).

### Gold market and gold funds

Two composed endpoints (`app/services/gold_market.py`,
`app/services/gold_funds.py`) replace a legacy pipeline (`gold_1.py` +
`gold_2.py`, previously run against a ClickHouse "mabna" feed and a ---
server that no longer exists) with the same two output tables, sourced
from what's actually available now:

- `GET /v1/gold/market` -- one row per instrument (`ons`, `geram18`,
  `geram24`, `sekee`, `govahi_sekke`, `govahi_shemsh`, `dollar`), each
  from a specific confirmed Atlas `(source, isin)` -- see
  `SYMBOL_SOURCES` in `gold_market.py`. `dollar` is an average of
  `wallex`'s USDTTMN and `tabdeal`'s dollar (two different instruments
  used as a proxy pair, not an exact match); both raw values are in
  `components` so nothing is hidden. `mesghal` has no current Atlas
  equivalent and is left out, not guessed at.
- `GET /v1/gold/funds` -- the 31 gold funds: live trade/order-book data
  (`last_trade`, `ask_price_1`, `bid_price_1`, `value`, `volume`, `nav`)
  from `market_fetcher`'s `all_tickers_info` Redis key (parsed by hand
  -- it's a `pandas.DataFrame.to_json()` blob, and Nexus doesn't
  otherwise depend on pandas), joined with NAV from Atlas's `tadbir`
  and `farabi` providers and this month's portfolio weights from
  `quant_db`'s `live.last_month_gold_compos` (a cross-project table,
  same pattern as `AtlasProvider` reading Atlas's own data). Only
  `nominal_bubble` (`last_trade/nav_live - 1`) is computed -- the
  legacy pipeline's intrinsic/sekke/shemsh bubble decomposition is
  deliberately not ported: it depends on unit assumptions (a "mesghal"
  price, a specific dollar rate) not yet validated against the new
  datasources.

`market_fetcher` (a separate, older project, `~/market_fetcher` on the
server) must be running for `/v1/gold/funds` to have live data --
`all_tickers_info` has no TTL, so if that service stops, this endpoint
silently starts serving an increasingly stale snapshot rather than
erroring. There is no staleness check on this yet.

## Quickstart

```bash
cp .env.example .env       # fill in NEXUS_PG_DSN / NEXUS_REDIS_URL
pip install -r requirements.txt
uvicorn app.main:app --reload
```

```bash
pytest                      # unit tests, no real Postgres/Redis needed --
                             # router tests use a FakeProvider, provider
                             # tests mock asyncpg and redis directly
```

## Prerequisites (server)

- Postgres reachable at `NEXUS_PG_DSN`. On the `alpha` quant box, this
  is the same socket-based, no-password DSN pattern Atlas uses
  (`postgresql://quant@/quant_db?host=/var/run/postgresql`) -- it
  peer-auths as OS user `quant` over the local Unix socket, and works
  only because Nexus runs as `quant` on the same box as Postgres.
- Redis reachable at `NEXUS_REDIS_URL` -- the same Redis instance Atlas
  writes to (`redis://127.0.0.1:6379/0` by default).
- Python: the existing `/opt/quant/envs/quant/bin/python` already has
  fastapi/uvicorn/pydantic/asyncpg/redis (with async support) installed
  -- no new env needed.
