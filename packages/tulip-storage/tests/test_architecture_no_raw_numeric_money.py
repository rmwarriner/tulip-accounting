"""Architecture test: model files must wrap Numeric columns in SqliteDecimal (#395).

``sqlalchemy.Numeric`` on SQLite has NUMERIC type affinity, which
silently stores Decimal values as IEEE-754 REAL — breaking exact
arithmetic and the per-currency balance triggers
(``trg_transactions_balanced_on_post`` et al.) for any transaction
with three or more legs.

``tulip_storage.models.base.SqliteDecimal`` is the project's TypeDecorator
that hides this footgun: it stores Decimal as scaled INT64 on SQLite and
passes Decimal through unchanged on Postgres. Every model column that
holds money must use it.

This test scans every ``packages/tulip-storage/src/tulip_storage/models/*.py``
file and rejects any direct ``mapped_column(Numeric(...))`` declaration.
The single legitimate ``Numeric`` reference is the one inside
``SqliteDecimal`` itself (in ``base.py``), which is excluded.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_MODELS_DIR: Final[Path] = (
    _REPO_ROOT / "packages" / "tulip-storage" / "src" / "tulip_storage" / "models"
)
# base.py defines SqliteDecimal and is the only file allowed to reference
# the raw Numeric class.
_EXCLUDED: Final[frozenset[str]] = frozenset({"base.py"})


def _raw_numeric_columns(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for any ``mapped_column(Numeric(...), ...)``.

    Matches the AST shape ``Call(func=Name('mapped_column'))`` whose first
    positional argument is ``Call(func=Name('Numeric'))``. Wrapping in
    ``SqliteDecimal(...)`` is fine — the type-decorator presents the same
    column-type interface but routes through the safe codec.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "mapped_column"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.Call)
            and isinstance(first.func, ast.Name)
            and first.func.id == "Numeric"
        ):
            hits.append((node.lineno, "mapped_column(Numeric(...))"))
    return hits


def test_no_raw_numeric_money_columns_in_models() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(_MODELS_DIR.glob("*.py")):
        if path.name in _EXCLUDED:
            continue
        hits = _raw_numeric_columns(path)
        if hits:
            offenders[path.name] = hits

    assert not offenders, (
        "mapped_column(Numeric(...)) is forbidden in tulip_storage.models — "
        "raw sqlalchemy.Numeric stores Decimal as IEEE-754 REAL on SQLite "
        "and breaks the per-currency balance trigger (#395). Wrap with "
        "tulip_storage.models.base.SqliteDecimal instead:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
