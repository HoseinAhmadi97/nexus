from __future__ import annotations

import abc
import dataclasses
import datetime as dt
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class PricePoint:
    """One provider's latest known price for one instrument."""

    isin: str
    source: str
    price: float | None
    updated_at: dt.datetime
    payload: dict[str, Any]


class PriceProvider(abc.ABC):
    """One backend Nexus can ask "what's the latest price for X".

    Nexus is not tied to Atlas -- a provider is anything that can list
    and look up price points it knows about. Atlas (via
    `atlas_provider.py`, reading `atlas.raw_ticks` directly over SQL,
    not by importing Atlas's Python package) is the first one, not the
    only one. Adding a second, unrelated provider later is a new file
    here plus one line registering it in `app/main.py` -- routers never
    need to know how many providers exist or where their data comes
    from.
    """

    #: Short label, becomes each PricePoint's `source` unless the
    #: provider itself distinguishes multiple upstream sources (as
    #: AtlasProvider does, since atlas.raw_ticks already spans several).
    name: str

    @abc.abstractmethod
    async def latest(self, isin: str) -> list[PricePoint]:
        """Every source's latest point for one isin. Empty if unknown."""
        raise NotImplementedError

    @abc.abstractmethod
    async def list_latest(self, source: str | None = None) -> list[PricePoint]:
        """Latest point per (isin, source) this provider currently knows,
        optionally narrowed to one source."""
        raise NotImplementedError
