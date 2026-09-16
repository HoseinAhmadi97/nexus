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

7. **`app/services/gold_market.py` and `gold_funds.py` are composed
   business logic, not providers.** They replace a legacy pipeline
   (`gold_1.py`/`gold_2.py`) whose own ClickHouse/SQL backends no
   longer exist on this server, sourced instead from what's actually
   available (confirmed symbol-by-symbol with the user, 2026-09-13 --
   see README "Gold market and gold funds"). Don't route these through
   `PriceProvider`: `SYMBOL_SOURCES` in `gold_market.py` deliberately
   picks a specific `(source, isin)` per instrument (the opposite of
   `/v1/prices/{isin}`'s "return every source" rule) precisely because
   this endpoint's whole point is composing one coherent answer, and
   `gold_funds.py` reads `market_fetcher`'s Redis key and
   `quant_db.live.last_month_gold_compos` directly -- neither fits the
   isin/price shape `PriceProvider` was built for.

8. **`gold_funds.py` depends on `market_fetcher` (`~/market_fetcher`, a
   separate, older project) actually running.** Its `all_tickers_info`
   Redis key has no TTL (same convention Atlas uses, for the same
   reason), so if that service stops, `/v1/gold/funds` doesn't error --
   it silently keeps serving an increasingly stale snapshot. There is
   no staleness check on this yet; don't assume the absence of an
   error means the data is current.

9. **Intrinsic value is a simplified port of the legacy pipeline** (see
   16 below). The rest of the legacy intrinsic/sekke/shemsh bubble decomposition
   is deliberately not ported.** Only `nominal_bubble`
   (`last_trade/nav - 1`, `nav` = farabi, see 15) is computed in `gold_funds.py` -- the
   fuller decomposition depends on unit assumptions (a "mesghal" price
   Atlas has no equivalent for, a specific dollar-rate convention) that
   haven't been validated against the new datasources. Don't add it
   back without validating those assumptions first; a wrong unit
   conversion silently producing plausible-looking numbers is exactly
   the bug class `tabdeal_fetcher.py` had (Atlas invariant 15).

10. **`/v1/gold/snapshot` is never built inside a request.** It is
    served from `app.state.gold_snapshot` (`SnapshotCache`), rebuilt by
    a background task on a timer. The public website polls it; if a
    change makes the endpoint compute anything per request, visitor
    traffic starts reaching Redis and Postgres. Keep building in
    `build_gold_snapshot()` and serving in the router separate.

11. **Composed gold endpoints read Atlas with `latest_for()`, not
    `list_latest()`.** They know exactly which `(source, isin)` pairs
    they need; `list_latest()` with no source scans the whole of
    `atlas.raw_ticks` every call and grows with the table. `latest_for()`
    keeps invariant 3 (Redis first, Postgres only for pairs Redis
    lacked). It is Atlas-specific, deliberately not on `PriceProvider`.

12. **Prices keep their source's unit, labelled with `unit`.** estjt,
    tabdeal and wallex are toman (`IRT`), IME and TSE funds are rial
    (`IRR`; IME `GoldBar` is rial per 100 mg), the ounce is `USD`.
    Nexus never converts; the consumer does, explicitly. A silent
    rial/toman mix-up produces numbers that look plausible and are 10x
    off.

13. **Instrument sources are confirmed with the user; don't swap them
    silently.** The original six plus dollar on 2026-09-13; `sekee_bahar`,
    `nim`, `rob`, `gerami` (estjt) and `mesghal` (tabdeal `gold_melt`,
    which tracks geram18 x 4.3318 within 0.3%) on 2026-09-16.

14. **`/v1/gold/nav-trend` stays separate from the snapshot.** Every
    page polls the snapshot; only the gold dashboard needs every fund's
    intraday NAV (~10 KB), and NAV moves about once a minute. Folding
    it into the snapshot would make every page on the site download it
    every 20 s. Same serving rules as invariant 10.

15. **farabi is the NAV of record** (the user's decision, 2026-09-16).
    `GoldFundRow.nav`, `nominal_bubble`, the snapshot's `nav` series and
    `/v1/gold/nav-trend` all use farabi. `nav_live` (TSE) and
    `nav_tadbir` stay in fund rows for comparison only. There is no
    fallback to another source when farabi is missing -- the bubble is
    None instead, so a number never silently changes its source.

16. **The gold fund market status is a schedule, not an inference:**
    Saturday-Wednesday 12:00-18:00 Tehran (`fund_market_open()` in
    `gold_snapshot.py`, given by the user 2026-09-16). Holidays are not
    known to Nexus.

17. **Certificate intrinsic value, fund intrinsic bubble and implied dollar
    live in `services/gold_intrinsic.py`**, ported from the legacy
    `calculute_bubble()` (gold_2.py) and simplified with the user on
    2026-09-16: one dollar (the market table's `dollar`), only the coin and bar
    certificates are valued, fund intrinsic bubble = coin weight x coin-cert
    bubble + bar weight x bar-cert bubble, implied dollar = dollar x (1 +
    bubble). The constants (9.5742, 4.6083, 705/750, 4.2492, /10) are the
    legacy ones -- don't change them without the user. Consumers show these
    numbers; they don't recompute them.

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
