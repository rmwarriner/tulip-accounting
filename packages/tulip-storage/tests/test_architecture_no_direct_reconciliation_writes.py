"""Architecture test: P5.1 chokepoint tables only written through their repos.

Per ADR-0004 §"Architecture tests" — direct ORM-model construction of the
seven new P5.1 tables (`Attachment`, `AttachmentLink`, `ImportBatch`,
`StatementLine`, `Reconciliation`, `ReconciliationMatch`, `CsvProfile`)
is rejected outside the model file itself, the model re-exports, the
matching repository, and the migration.

Mirrors the pattern in ``test_architecture_no_direct_shadow_writes.py``.
The domain layer (`tulip-core`) doesn't have analogues for these — they're
storage-only types in P5.1; the domain `StatementLine` etc. land in P5.3
under ``tulip_core.reconciliation``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

_BANNED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Attachment",
        "AttachmentLink",
        "ImportBatch",
        "StatementLine",
        "Reconciliation",
        "ReconciliationMatch",
        "CsvProfile",
    }
)

_STORAGE_MODEL_MODULES: Final[frozenset[str]] = frozenset(
    {
        "tulip_storage.models",
        "tulip_storage.models.attachment",
        "tulip_storage.models.attachment_link",
        "tulip_storage.models.import_batch",
        "tulip_storage.models.statement_line",
        "tulip_storage.models.reconciliation",
        "tulip_storage.models.reconciliation_match",
        "tulip_storage.models.csv_profile",
    }
)

_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # Model files — they declare the classes.
        "tulip-storage/src/tulip_storage/models/attachment.py",
        "tulip-storage/src/tulip_storage/models/attachment_link.py",
        "tulip-storage/src/tulip_storage/models/import_batch.py",
        "tulip-storage/src/tulip_storage/models/statement_line.py",
        "tulip-storage/src/tulip_storage/models/reconciliation.py",
        "tulip-storage/src/tulip_storage/models/reconciliation_match.py",
        "tulip-storage/src/tulip_storage/models/csv_profile.py",
        # Re-exports.
        "tulip-storage/src/tulip_storage/models/__init__.py",
        # Repositories — the legitimate writers.
        "tulip-storage/src/tulip_storage/repositories/attachment.py",
        "tulip-storage/src/tulip_storage/repositories/attachment_link.py",
        "tulip-storage/src/tulip_storage/repositories/import_batch.py",
        "tulip-storage/src/tulip_storage/repositories/statement_line.py",
        "tulip-storage/src/tulip_storage/repositories/reconciliation.py",
        "tulip-storage/src/tulip_storage/repositories/reconciliation_match.py",
        "tulip-storage/src/tulip_storage/repositories/csv_profile.py",
        "tulip-storage/src/tulip_storage/repositories/__init__.py",
        # API router needs the row type for the _resolve helper's return
        # annotation (writes still route through CsvProfileRepository;
        # the import is read-only, mirroring how P5.0's void-link guard
        # allowed the API router for read-side access).
        "tulip-api/src/tulip_api/routers/csv_profiles.py",
        # Reconciliation summary report (P7.1) reads the model for
        # display; never writes. Same justification as csv_profiles.py.
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


def _is_type_checking_block(node: ast.AST) -> bool:
    """True iff ``node`` is ``if TYPE_CHECKING:`` (or ``if typing.TYPE_CHECKING:``)."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return True
    return False


def _collect_type_checking_lines(tree: ast.Module) -> set[int]:
    """Return the line numbers covered by every ``if TYPE_CHECKING:`` block in ``tree``."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if _is_type_checking_block(node):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    covered.add(child.lineno)
    return covered


def _illegal_storage_model_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` pairs that import P5.1 storage-layer models.

    Imports inside ``if TYPE_CHECKING:`` blocks are excluded — they don't
    create a runtime path to the model class, so they can't construct or
    write to one. This matches mypy's standard idiom for type-only imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    type_checking_lines = _collect_type_checking_lines(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _STORAGE_MODEL_MODULES:
            if node.lineno in type_checking_lines:
                continue
            for alias in node.names:
                if alias.name in _BANNED_NAMES:
                    hits.append((node.lineno, alias.name))
    return hits


def test_no_direct_storage_p51_model_use_outside_allowlist() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _illegal_storage_model_imports(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct imports of P5.1 storage-layer model classes outside the "
        "repository allowlist — route through the repos so tenant scoping, "
        "audit-log writes, and chokepoint guarantees stay correct:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
