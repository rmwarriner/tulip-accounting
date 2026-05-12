"""Architecture test: only ReconciliationRepository writes reconciliation denorms.

Per ADR-0004 §Q7 / §"Implementation notes": ``transactions.reconciled_at``,
``transactions.reconciliation_id``, and
``transactions.carried_forward_from_reconciliation_id`` are denormalisations
of the ``reconciliations`` aggregate. The truth lives there; the columns
on ``transactions`` exist for fast lookup.

Direct writes to those columns from anywhere except
``ReconciliationRepository`` (and the carry-forward chokepoint, which lands
in P5.4) are banned. ``transactions.imported_from_id`` and
``transactions.cleared_at`` have analogous chokepoints in P5.2 and P5.4
respectively; this guard expands to cover them then.

Mirrors ``test_architecture_no_direct_void_link_writes.py`` (P5.0).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

_GUARDED: Final[frozenset[str]] = frozenset(
    {
        "reconciled_at",
        "reconciliation_id",
        "carried_forward_from_reconciliation_id",
    }
)

#: Files allowed to write the guarded columns. The repository is the only
#: place that *writes* to the underlying DB column. Other places may *read*
#: (e.g., API serialization), but writes are chokepointed.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        "tulip-storage/src/tulip_storage/repositories/reconciliation.py",
        # reconciliation_match.py uses ``reconciliation_id`` as the FK column
        # on the ``reconciliation_matches`` table (not on ``transactions``);
        # the kwarg-name collision is unavoidable without context-aware AST.
        "tulip-storage/src/tulip_storage/repositories/reconciliation_match.py",
        "tulip-storage/src/tulip_storage/models/reconciliation.py",
        "tulip-storage/src/tulip_storage/models/reconciliation_match.py",
        "tulip-storage/src/tulip_storage/models/transaction.py",
        "tulip-storage/src/tulip_storage/migrations/versions/"
        "20260505_1000_f4a6b9c2e7d3_add_imports_reconciliations.py",
        # The reconciliation API surface (P5.4.b) reads/passes
        # ``reconciliation_id`` as the entity identifier (path param, FK
        # column on reconciliation_matches, audit-log entity_id, etc.) —
        # never as a write to ``transactions.reconciliation_id``. Same
        # collision the AST checker can't disambiguate without context.
        "tulip-api/src/tulip_api/routers/reconciliations.py",
        "tulip-api/src/tulip_api/services/reconciliation_match.py",
        # P7.1 reconciliation summary report reads ``reconciliation_id`` as
        # the entity id for display; never writes. Same kwarg-name collision
        # the AST checker can't disambiguate without context.
        "tulip-reports/src/tulip_reports/reports/reconciliation_summary.py",
    }
)


def _python_source_files() -> list[Path]:
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _writes_to_guarded(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(lineno, kind, column_name)`` for each guarded write."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        # Attribute assignment.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in _GUARDED:
                    hits.append((node.lineno, "assign", target.attr))
        if isinstance(node, ast.AugAssign) and (
            isinstance(node.target, ast.Attribute) and node.target.attr in _GUARDED
        ):
            hits.append((node.lineno, "augassign", node.target.attr))
        # Keyword arg in a call.
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _GUARDED:
                    hits.append((node.lineno, "kwarg", kw.arg))
    return hits


def test_no_direct_reconciliation_denorm_writes_outside_allowlist() -> None:
    offenders: dict[str, list[tuple[int, str, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _writes_to_guarded(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct writes to reconciliation-denormalisation columns on "
        "transactions outside ReconciliationRepository — route through the "
        "repo so the truth (reconciliations table) and the denorm stay "
        "consistent (per ADR-0004 §Q7):\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
