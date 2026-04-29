"""Alembic env script for tulip-storage.

Reads the DB URL from `TULIP_DATABASE_URL` if present, otherwise falls back
to the value in alembic.ini. Uses tulip_storage.models.Base.metadata as the
source of truth so `alembic revision --autogenerate` works.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from tulip_storage.models import Base

# Alembic Config object provides access to the .ini file values.
config = context.config

# Configure logging from the alembic.ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment if set.
db_url = os.environ.get("TULIP_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL DDL without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations to the configured DB engine."""
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Enable FK enforcement on SQLite (off by default).
    @event.listens_for(connectable, "connect")
    def _enable_fk(dbapi_conn: object, _record: object) -> None:
        if connectable.dialect.name == "sqlite":
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
