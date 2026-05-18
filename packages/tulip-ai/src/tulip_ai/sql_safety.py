"""Validate + rewrite model-emitted SQL before execution (ADR-0005 §Q3).

The NL-query capability lets the AI emit raw SQL. That is a security
surface, so every emitted statement goes through this module before it
reaches a real database connection:

1. **Parse** with sqlglot in the ``sqlite`` dialect. A parse error is a hard
   reject — the model is supposed to emit SQL, not free-text-with-a-side-of-prose.
2. **Validate**: single statement, single ``SELECT`` (no INSERT / UPDATE /
   DELETE / DDL / PRAGMA / ATTACH), no subquery / CTE referencing a table
   outside the allowlist, no ``SELECT INTO``, no UNION pulling from a
   non-allowed table.
3. **Rewrite**: every ``FROM ai_view_X`` (and ``JOIN ai_view_X``) is replaced
   with a subquery that pins the household scope. The subquery selects from
   the canonical underlying tables and adds ``WHERE household_id = :hh``.
4. **Bound**: append ``LIMIT 100`` if the statement doesn't already cap rows.

The result is a SQL string + parameter dict that can be executed against
a read-only SQLite connection.

Allowlisted views in v1: ``ai_view_transactions``. Adding more is a
matter of registering them in ``AI_VIEWS`` below; the rest of the
module is view-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    """The model emitted SQL we won't execute."""


# Each entry maps an AI-facing view name to:
# (a) a SQL fragment selecting from the canonical tables with the columns
#     the schema-card promises the model.
# (b) the column names the view exposes; used by the schema-card builder.
@dataclass(frozen=True, slots=True)
class AIView:
    """Static metadata describing one AI view (ADR-0005 §Q3 NL query schema)."""

    name: str
    columns: tuple[tuple[str, str], ...]  # (column_name, sqlite_type) tuples
    select_fragment: str  # SELECT ... FROM ... — no WHERE; the rewriter adds tenant scope


_TRANSACTIONS_VIEW = AIView(
    name="ai_view_transactions",
    columns=(
        ("transaction_id", "TEXT"),
        ("date", "DATE"),
        ("description", "TEXT"),
        ("amount", "NUMERIC"),
        ("currency", "TEXT"),
        ("account_code", "TEXT"),
        ("account_name", "TEXT"),
        ("account_type", "TEXT"),
        ("status", "TEXT"),
        ("reconciled_at", "DATETIME"),
    ),
    # ``p.amount * 1.0 / 1e8``: ``postings.amount`` is stored as scaled
    # INT64 on SQLite to keep the per-currency balance trigger exact
    # (#395). For AI consumption we expose the original Decimal value —
    # multiplying by 1.0 forces REAL division so the AI gets ``87.42``
    # not ``8742000000``. The view is display-only; ledger arithmetic
    # never goes through it.
    select_fragment=(
        "SELECT t.id AS transaction_id, t.date AS date, t.description AS description, "
        "(p.amount * 1.0 / 100000000.0) AS amount, p.currency AS currency, "
        "a.code AS account_code, a.name AS account_name, a.type AS account_type, "
        "t.status AS status, t.reconciled_at AS reconciled_at "
        "FROM transactions t "
        "JOIN postings p ON p.household_id = t.household_id AND p.transaction_id = t.id "
        "JOIN accounts a ON a.household_id = p.household_id AND a.id = p.account_id"
    ),
)

AI_VIEWS: dict[str, AIView] = {
    _TRANSACTIONS_VIEW.name: _TRANSACTIONS_VIEW,
}


@dataclass(frozen=True, slots=True)
class SafeSQL:
    """Result of ``validate_and_rewrite`` — ready to execute."""

    sql: str
    parameters: dict[str, object]


def schema_card() -> str:
    """Human-readable DDL summary of the AI views — fed to the model as prompt context."""
    parts: list[str] = []
    for view in AI_VIEWS.values():
        cols = ",\n  ".join(f"{col} {ty}" for col, ty in view.columns)
        parts.append(f"CREATE VIEW {view.name} (\n  {cols}\n);")
    return "\n\n".join(parts)


def _walk_tables(tree: exp.Expression) -> list[exp.Table]:
    return list(tree.find_all(exp.Table))


def _reject_dangerous_node_types(tree: exp.Expression) -> None:
    """Reject anything that mutates state or escapes the SELECT box.

    The check is structural: even a single ``ATTACH``, ``PRAGMA``,
    ``INSERT``, etc. anywhere in the tree is a reject. SQLite is
    permissive about expression contexts; we are not.
    """
    dangerous = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Pragma,
        exp.Command,  # ATTACH, DETACH, VACUUM, etc.
    )
    for node in tree.walk():
        if isinstance(node, dangerous):
            raise UnsafeSQLError(
                f"emitted SQL contains a {type(node).__name__} node; "
                "only single SELECT statements are allowed"
            )


def validate_and_rewrite(emitted_sql: str, *, household_id: str) -> SafeSQL:
    """Parse + validate + rewrite ``emitted_sql`` for ``household_id``.

    Returns the safe SQL + parameter dict for execution. Raises
    :class:`UnsafeSQLError` for anything we won't run.
    """
    if not emitted_sql or not emitted_sql.strip():
        raise UnsafeSQLError("emitted SQL is empty")

    # Multi-statement scripts are not allowed: parse_one rejects them.
    try:
        tree = sqlglot.parse_one(emitted_sql, dialect="sqlite")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQLError(f"could not parse emitted SQL: {exc}") from exc

    if tree is None:
        raise UnsafeSQLError("parser returned no statement")

    # Top-level must be SELECT (allow Select or set-ops like Union over SELECTs).
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQLError(f"top-level statement must be SELECT (got {type(tree).__name__})")

    _reject_dangerous_node_types(tree)

    # Every table reference must hit an allowlisted AI view name.
    referenced = _walk_tables(tree)
    if not referenced:
        raise UnsafeSQLError("emitted SQL references no tables")
    for table in referenced:
        name = table.name
        if name not in AI_VIEWS:
            raise UnsafeSQLError(
                f"table {name!r} is not in the AI view allowlist ({sorted(AI_VIEWS)})"
            )

    # Rewrite each ai_view_X reference to a tenant-scoped subquery. The
    # subquery select_fragment + ``WHERE household_id = :hh`` is wrapped in
    # parens with the original alias preserved so the surrounding SQL keeps
    # working.
    for table in referenced:
        view = AI_VIEWS[table.name]
        scoped_subquery = f"({view.select_fragment} WHERE t.household_id = :household_id)"
        alias = table.alias or table.name
        new_node = sqlglot.parse_one(f"{scoped_subquery} AS {alias}", dialect="sqlite")
        table.replace(new_node)

    # Bound row count if the model didn't.
    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree.set("limit", exp.Limit(expression=exp.Literal.number(100)))

    rewritten = tree.sql(dialect="sqlite")
    return SafeSQL(sql=rewritten, parameters={"household_id": household_id})


__all__ = [
    "AI_VIEWS",
    "AIView",
    "SafeSQL",
    "UnsafeSQLError",
    "schema_card",
    "validate_and_rewrite",
]
