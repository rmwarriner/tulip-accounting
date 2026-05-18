"""Declarative base + shared SQLAlchemy types for tulip-storage.

UUIDs are stored as CHAR(36) on SQLite for portability; on Postgres we'll
swap to native UUID. Money amounts go through :class:`SqliteDecimal`,
which stores Decimal as scaled INT64 on SQLite (sidestepping the
NUMERIC-affinity-degrades-to-REAL footgun that breaks the per-currency
balance triggers — see #395) and passes Decimal through unchanged on
Postgres, where NUMERIC arithmetic is already exact.

Naming convention is set so Alembic auto-generates stable constraint names,
which keeps `alembic upgrade head; alembic downgrade base` reversible.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, Dialect, MetaData, Numeric, TypeDecorator
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


class SqliteDecimal(TypeDecorator[Decimal]):
    """Decimal column that stores as scaled INT64 on SQLite (#395).

    SQLAlchemy's ``Numeric`` maps to SQLite's NUMERIC type affinity, which
    stores Decimal values as IEEE-754 REAL the moment they round-trip
    through the driver. Three-or-more-leg balanced transactions then trip
    the ``trg_transactions_balanced_on_post`` trigger because
    ``SUM(amount)`` lands on a tiny rounding residue rather than exactly
    zero. Two-leg transactions accidentally pass because ``+x + -x == 0``
    in IEEE-754 when ``|x|`` is identical on both sides.

    The decorator binds Decimal as ``int(value * 10**scale)`` (banker's
    rounding past ``scale``) and reverses on read, so SQLite stores plain
    INT64 and ``SUM`` is exact integer arithmetic. The trigger's
    ``HAVING SUM(amount) != 0`` then agrees with Decimal semantics.

    On any non-SQLite dialect the decorator is a no-op — Postgres NUMERIC
    is already exact, so we pass Decimal through unchanged. ``impl`` stays
    ``Numeric`` so the column type in non-SQLite DDL is ``NUMERIC(20, 8)``.

    The scale is configurable so the same decorator covers both the
    ``Numeric(20, 8)`` money columns and the smaller-scale
    ``Numeric(12, 6)`` cost-tracking column.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 20, scale: int = 8) -> None:
        """Configure ``precision`` / ``scale`` to match the original Numeric."""
        super().__init__()
        self._precision = precision
        self._scale = scale
        self._factor = Decimal(10) ** scale

    @property
    def python_type(self) -> type:
        """Decimal in every dialect; the storage format differs."""
        return Decimal

    def load_dialect_impl(self, dialect: Dialect) -> Any:  # noqa: ANN401
        """Use BigInteger on SQLite (INT64 affinity); Numeric elsewhere."""
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self._precision, self._scale, asdecimal=True))

    def process_bind_param(self, value: object, dialect: Dialect) -> int | Decimal | None:
        """Decimal → scaled int on SQLite, passthrough on Postgres."""
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        if dialect.name == "sqlite":
            scaled = (value * self._factor).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
            return int(scaled)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        """Scaled int → Decimal on SQLite, ensure Decimal elsewhere."""
        if value is None:
            return None
        if dialect.name == "sqlite":
            # ``value`` is int (BigInteger affinity). Decimal(int).scaleb(-n)
            # is exact: 6_201_000_000.scaleb(-8) == Decimal('62.01').
            return Decimal(cast("int", value)).scaleb(-self._scale)
        return value if isinstance(value, Decimal) else Decimal(str(value))
