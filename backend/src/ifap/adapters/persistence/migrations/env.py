"""Alembic environment. Migrations always run *online* on a connection supplied by
`ifap.adapters.persistence.schema.migrate` (no alembic.ini, no URLs in config files)."""

from __future__ import annotations

from alembic import context
from sqlalchemy.engine import Connection

from ifap.adapters.persistence.sqlalchemy_repository import Base

connection: Connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch ALTERs
)
with context.begin_transaction():
    context.run_migrations()
