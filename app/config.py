from __future__ import annotations

import dataclasses
import os

from dotenv import load_dotenv


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    postgres_dsn: str
    redis_url: str
    host: str
    port: int
    #: How often /v1/gold/snapshot is rebuilt in the background.
    snapshot_refresh_seconds: float = 10.0


def load_settings() -> Settings:
    load_dotenv()  # .env, if present, populates os.environ first
    return Settings(
        postgres_dsn=os.environ["NEXUS_PG_DSN"],
        redis_url=os.environ.get("NEXUS_REDIS_URL", "redis://127.0.0.1:6379/0"),
        host=os.environ.get("NEXUS_HOST", "127.0.0.1"),
        port=int(os.environ.get("NEXUS_PORT", "8100")),
        snapshot_refresh_seconds=float(os.environ.get("NEXUS_SNAPSHOT_REFRESH_SECONDS", "10")),
    )
