"""Architecture test: void-link writes go through TransactionRepository (P5.0).

Per ADR-0004, ``transactions.voided_by_transaction_id`` is the truth of
"this transaction has been reversed." It must only ever be set by
:class:`tulip_storage.repositories.TransactionRepository.persist_reversal`,
which atomically writes the reversal sibling and links the source row.

The check rejects any source file under ``packages/*/src/`` (tests
excluded; tests legitimately seed rows for setup) that uses
``voided_by_transaction_id`` as a keyword argument or attribute assignment.
Reading the column is permitted everywhere — the API handler reads it for
the already-voided pre-flight check.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

_GUARDED: Final[str] = "voided_by_transaction_id"

#: Files allowed to mention ``voided_by_transaction_id`` as a kwarg or
#: attribute assignment. The repository is the only place that *writes* to
#: the underlying DB column; the API router and the API schemas legitimately
#: pass it as a kwarg to read-side objects (``TransactionRead`` Pydantic
#: model, ``TransactionAlreadyVoidedError`` Problem Details extension).
#: Paths are relative to ``packages/``.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # The repository chokepoint — the only legitimate DB writer.
        "tulip-storage/src/tulip_storage/repositories/transaction.py",
        # The ORM model declares the column.
        "tulip-storage/src/tulip_storage/models/transaction.py",
        # The migration adds the column.
        "tulip-storage/src/tulip_storage/migrations/versions/"
        "20260504_2000_e7d2a4f8c1b9_add_transaction_void_links.py",
        # API surface: read-side responses + Problem extensions. No DB writes.
        "tulip-api/src/tulip_api/schemas/transaction.py",
        "tulip-api/src/tulip_api/errors.py",
        "tulip-api/src/tulip_api/routers/transactions.py",
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


def _writes_to_void_link(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, kind)`` pairs where the file writes the void link.

    Two write shapes are considered:
    - ``foo.voided_by_transaction_id = ...`` (attribute assignment).
    - ``...(voided_by_transaction_id=...)`` (keyword-arg in a call).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Attribute assignment.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == _GUARDED:
                    hits.append((node.lineno, "assign"))
        if isinstance(node, ast.AugAssign) and (
            isinstance(node.target, ast.Attribute) and node.target.attr == _GUARDED
        ):
            hits.append((node.lineno, "augassign"))
        # Keyword arg in a call (covers both .values(voided_...=) and
        # MyModel(voided_...=) construction).
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == _GUARDED:
                    hits.append((node.lineno, "kwarg"))
    return hits


def test_no_direct_void_link_writes_outside_allowlist() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _writes_to_void_link(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct writes to transactions.voided_by_transaction_id outside the "
        "TransactionRepository.persist_reversal chokepoint — route through "
        "the repo so the void link and the reversal-sibling write stay "
        "atomic (per ADR-0004 §P5.0):\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
