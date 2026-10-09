"""Engine URL translation and Alembic migrations (upgrade, downgrade, legacy stamping)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from ifap.adapters.persistence import schema
from ifap.adapters.persistence.schema import (
    BASELINE_REVISION,
    create_engine,
    downgrade,
    migrate,
    translate_url,
)
from ifap.adapters.persistence.sqlalchemy_repository import Base
from ifap.config.settings import DatabaseSettings

NEON = (
    "postgresql://user:secret@ep-cool-name-123.eu-central-1.aws.neon.tech/ifap"
    "?sslmode=require&channel_binding=require"
)


def test_neon_url_is_translated_for_asyncpg() -> None:
    url, connect_args = translate_url(NEON)
    assert url.drivername == "postgresql+asyncpg"
    assert not dict(url.query)
    assert url.password == "secret"  # noqa: S105 - fake credential in a test fixture
    assert connect_args == {"ssl": "require"}


def test_pooled_neon_endpoint_disables_statement_cache() -> None:
    pooled = NEON.replace("ep-cool-name-123", "ep-cool-name-123-pooler")
    _, connect_args = translate_url(pooled)
    assert connect_args == {"ssl": "require", "statement_cache_size": 0}


def test_plain_and_sqlite_urls_pass_through() -> None:
    url, connect_args = translate_url("postgres://u:p@localhost/db?sslmode=disable")
    assert url.drivername == "postgresql+asyncpg"
    assert not connect_args
    sqlite, sqlite_args = translate_url("sqlite+aiosqlite:///x.db")
    assert sqlite.drivername == "sqlite+aiosqlite"
    assert not sqlite_args


def _engine(tmp_path: Path) -> AsyncEngine:
    return create_engine(DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"))


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(lambda c: set(inspect(c).get_table_names()))


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        result = await connection.execute(text("select version_num from alembic_version"))
        return result.scalar_one_or_none()


async def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    await migrate(engine)
    assert {"questionnaires", "alembic_version"} <= await _tables(engine)
    assert await _revision(engine) == BASELINE_REVISION
    await migrate(engine)  # idempotent
    await downgrade(engine, "base")
    assert "questionnaires" not in await _tables(engine)
    await engine.dispose()


async def test_pre_alembic_database_is_stamped_not_recreated(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    async with engine.begin() as connection:  # what the old create_all start-up produced
        await connection.run_sync(Base.metadata.create_all)
    await migrate(engine)
    assert await _revision(engine) == BASELINE_REVISION
    await engine.dispose()


async def test_migrations_match_the_orm_models(tmp_path: Path) -> None:
    """Fails when a model changes without a migration (run `alembic revision --autogenerate`)."""
    engine = _engine(tmp_path)
    await migrate(engine)

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        assert await connection.run_sync(diff) == []
    await engine.dispose()


def test_postgres_engine_gets_a_connect_timeout() -> None:
    engine = create_engine(DatabaseSettings(url=NEON, connect_timeout_seconds=7))
    assert engine.dialect.name == "postgresql"
    sqlite = create_engine(DatabaseSettings(url="sqlite+aiosqlite:///x.db"))
    assert sqlite.dialect.name == "sqlite"


async def test_startup_migration_retries_transient_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    failures: list[OSError] = [ConnectionResetError("reset by peer"), TimeoutError("timed out")]
    real_migrate = schema.migrate

    async def flaky_migrate(target: AsyncEngine, revision: str = "head") -> None:
        if failures:
            raise failures.pop(0)
        await real_migrate(target, revision)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(schema, "migrate", flaky_migrate)
    settings = DatabaseSettings(startup_attempts=3, startup_backoff_seconds=2)
    await schema.migrate_with_retries(engine, settings, sleep=fake_sleep)
    assert sleeps == [2, 4]
    assert await _revision(engine) == BASELINE_REVISION

    failures.extend([OSError("down")] * 3)
    with pytest.raises(OSError, match="down"):
        await schema.migrate_with_retries(engine, settings, sleep=fake_sleep)
    await engine.dispose()
