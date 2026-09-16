from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import time
from typing import Awaitable, Callable

from pydantic import BaseModel

log = logging.getLogger("nexus.snapshot")


class SnapshotCache:
    """A document rebuilt on a timer and served as pre-encoded bytes.

    Built for endpoints a public website polls: requests never trigger a
    build, so upstream load is one build per interval no matter how many
    visitors there are. The JSON is encoded and gzipped once per build,
    and the ETag lets an unchanged poll end in a body-less 304.

    If a build fails, the last good document keeps being served; its own
    timestamp tells the client how old it is.
    """

    def __init__(self, build: Callable[[], Awaitable[BaseModel]], interval: float) -> None:
        self._build = build
        self.interval = interval
        self.body: bytes | None = None
        self.gzip_body: bytes | None = None
        self.etag: str | None = None
        self.built_at: float | None = None
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        async with self._lock:
            started = time.monotonic()
            model = await self._build()
            body = model.model_dump_json().encode("utf-8")
            etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
            if etag != self.etag:
                self.body = body
                self.gzip_body = gzip.compress(body, compresslevel=6)
                self.etag = etag
            self.built_at = time.time()
            log.debug("snapshot built in %.2fs, %d bytes", time.monotonic() - started, len(body))

    async def ensure(self) -> None:
        """Build once if nothing has been built yet (first request right
        after startup, before the background loop's first pass lands)."""
        if self.body is None:
            await self.refresh()

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("snapshot build failed; serving the previous one")
            await asyncio.sleep(self.interval)
