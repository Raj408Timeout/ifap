"""Database engine construction and schema migration.

* `create_engine` accepts the connection string exactly as Neon / Cloud SQL / Supabase show
  it (`postgresql://...?sslmode=require&channel_binding=require`) and translates it for the
  asyncpg driver, which takes TLS settings as connect arguments rather than URL parameters.
* `migrate` runs Alembic to `head` on start-up - the same code path for SQLite (local, tests)
  and PostgreSQL (cloud). A database created by the old `create_all` start-up is detected and
  stamped instead of re-created.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ifap.config.settings import DatabaseSettings
from ifap.observability.logging import get_logger

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BASELINE_REVISION = "0001"
_ASYNC_POSTGRES = "postgresql+asyncpg"
_URL_ONLY_PARAMS = ("sslmode", "channel_binding")  # libpq parameters asyncpg does not accept
_TRANSIENT = (OperationalError, DBAPIError, OSError, TimeoutError)
_log = get_logger(__name__)


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    url, connect_args = translate_url(settings.url)
    if url.drivername == _ASYNC_POSTGRES:
        connect_args["timeout"] = settings.connect_timeout_seconds
    return create_async_engine(
        url,
        echo=settings.echo,
        connect_args=connect_args,
        pool_pre_ping=True,  # serverless Postgres (Neon) closes idle connections
    )


def translate_url(raw: str) -> tuple[URL, dict[str, object]]:
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername=_ASYNC_POSTGRES)
    if url.drivername != _ASYNC_POSTGRES:
        return url, {}
    connect_args: dict[str, object] = {}
    sslmode = url.query.get("sslmode")
    if isinstance(sslmode, str) and sslmode != "disable":
        connect_args["ssl"] = sslmode
    if url.host and "-pooler" in url.host:
        connect_args["statement_cache_size"] = 0  # PgBouncer transaction mode
    return url.difference_update_query(_URL_ONLY_PARAMS), connect_args


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, revision)


async def migrate_with_retries(
    engine: AsyncEngine,
    settings: DatabaseSettings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Start-up migration that tolerates a database that is waking up or briefly unreachable."""
    attempt = 1
    while True:
        try:
            await migrate(engine)
            return
        except _TRANSIENT as exc:
            if attempt >= settings.startup_attempts:
                raise
            delay = settings.startup_backoff_seconds * attempt
            _log.warning("database.unavailable", attempt=attempt, retry_in=delay, error=str(exc))
            await sleep(delay)
            attempt += 1


async def downgrade(engine: AsyncEngine, revision: str) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_downgrade, revision)


def _config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def _upgrade(connection: Connection, revision: str) -> None:
    config = _config(connection)
    tables = set(inspect(connection).get_table_names())
    if "questionnaires" in tables and "alembic_version" not in tables:
        command.stamp(config, BASELINE_REVISION)  # pre-Alembic database
    command.upgrade(config, revision)


def _downgrade(connection: Connection, revision: str) -> None:
    command.downgrade(_config(connection), revision)
