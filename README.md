# Nexus

The API layer for the server: builds and serves the APIs different
parts of the organization need. Not tied to any one upstream data
source -- Atlas (the raw price/time-series ingestion layer) is the
first backend it reads from, not the only one it's meant to.

## Structure

```
app/
|-- main.py               # FastAPI app, lifespan wires up providers
|-- config.py              # Settings: DSN, host, port from env/.env
|-- schemas.py              # Pydantic response models
|-- providers/
|   |-- base.py            # PriceProvider ABC + PricePoint -- the only contract
|   `-- atlas_provider.py  # reads atlas.raw_ticks over SQL
`-- routers/
    `-- prices.py           # GET /v1/prices, /v1/prices/{isin}

deploy/nexus.service        # systemd unit
tests/
docs/architecture.md
```

## Why a provider abstraction

Routers never talk to a database directly -- they ask
`request.app.state.providers`, a dict of `PriceProvider` instances, for
price points. `AtlasProvider` is the first one, reading
`atlas.raw_ticks` directly over SQL (Nexus depends on Atlas's *data
contract* -- the table's columns -- not on Atlas's Python package;
they're separate deployable projects). A second, unrelated data source
in the future is a new file in `app/providers/` plus one line
registering it in `app/main.py`'s `lifespan()` -- routers and schemas
never need to change. See `app/providers/base.py`.

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

## Quickstart

```bash
cp .env.example .env       # fill in NEXUS_PG_DSN
pip install -r requirements.txt
uvicorn app.main:app --reload
```

```bash
pytest                      # unit tests, no real Postgres needed --
                             # router tests use a FakeProvider, provider
                             # tests mock asyncpg directly
```

## Prerequisites (server)

- Postgres reachable at `NEXUS_PG_DSN`. On the `alpha` quant box, this
  is the same socket-based, no-password DSN pattern Atlas uses
  (`postgresql://quant@/quant_db?host=/var/run/postgresql`) -- it
  peer-auths as OS user `quant` over the local Unix socket, and works
  only because Nexus runs as `quant` on the same box as Postgres.
- Python: the existing `/opt/quant/envs/quant/bin/python` already has
  fastapi/uvicorn/pydantic/asyncpg installed -- no new env needed.
