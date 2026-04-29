"""Declarative base + shared SQLAlchemy types for tulip-storage.

UUIDs are stored as CHAR(36) on SQLite for portability; on Postgres we'll
swap to native UUID. Money amounts use Numeric(20, 8) per ARCHITECTURE §4.

Naming convention is set so Alembic auto-generates stable constraint names,
which keeps `alembic upgrade head; alembic downgrade base` reversible.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CHAR, Dialect, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with stable constraint naming for Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class GUID(TypeDecorator[UUID]):
    """Platform-agnostic UUID stored as CHAR(36) string.

    Round-trips python.UUID values. A future migration to Postgres can swap
    this for `postgresql.UUID(as_uuid=True)` without changing call sites.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        """Convert a python value into the string stored in the DB."""
        del dialect
        if value is None:
            return None
        if isinstance(value, UUID):
            return str(value)
        return str(UUID(str(value)))

    def process_result_value(self, value: object, dialect: Dialect) -> UUID | None:
        """Convert a string from the DB back into a python.UUID."""
        del dialect
        if value is None:
            return None
        return value if isinstance(value, UUID) else UUID(str(value))
