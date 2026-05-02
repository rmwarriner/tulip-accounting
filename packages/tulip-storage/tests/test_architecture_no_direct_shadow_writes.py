"""Architecture test: shadow-ledger writes go through ShadowTransactionRepository.

Per ADR-0001, the shadow ledger and the main ledger must stay in lockstep.
The single chokepoint is :class:`tulip_storage.repositories.ShadowTransactionRepository`,
which co-ordinates the header insert, the postings insert, and the balance
trigger fire on the status transition. Direct construction of
``tulip_storage.models.ShadowTransaction`` / ``ShadowPosting`` model
instances elsewhere in the codebase would let a caller bypass that flow
and emit invalid shadow rows.

The domain-layer value objects with the same names
(``tulip_core.allocation.ShadowTransaction``) are pure value objects with
no DB attachment and are deliberately not banned — that's the type the
rest of the codebase uses to *describe* a shadow tx before handing it to
the repo.

This test scans every source file under ``packages/*/src/`` (tests are
excluded; tests legitimately construct rows for setup) and rejects any
import of the storage-layer model classes outside the allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

_BANNED_NAMES: Final[frozenset[str]] = frozenset({"ShadowTransaction", "ShadowPosting"})

#: Modules whose ``ShadowTransaction``/``ShadowPosting`` are the storage-layer ORM models.
_STORAGE_MODEL_MODULES: Final[frozenset[str]] = frozenset(
    {
        "tulip_storage.models",
        "tulip_storage.models.shadow_transaction",
        "tulip_storage.models.shadow_posting",
    }
)

#: Files allowed to reference the storage-layer model classes. Paths are relative to ``packages/``.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # The model classes themselves.
        "tulip-storage/src/tulip_storage/models/shadow_transaction.py",
        "tulip-storage/src/tulip_storage/models/shadow_posting.py",
        # Re-exports.
        "tulip-storage/src/tulip_storage/models/__init__.py",
        # The repository — the only legitimate place that constructs them.
        "tulip-storage/src/tulip_storage/repositories/shadow_transaction.py",
        "tulip-storage/src/tulip_storage/repositories/__init__.py",
    }
)


def _python_source_files() -> list[Path]:
    """Yield every .py file under packages/*/src/."""
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _illegal_storage_model_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` pairs that import storage-layer Shadow models.

    Only ``from tulip_storage.models[.X] import ShadowTransaction|ShadowPosting``
    counts. Imports of the same names from ``tulip_core.allocation`` are
    fine — those are pure domain value objects.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _STORAGE_MODEL_MODULES:
            for alias in node.names:
                if alias.name in _BANNED_NAMES:
                    hits.append((node.lineno, alias.name))
    return hits


def test_no_direct_storage_shadow_model_use_outside_allowlist() -> None:
    """Storage-layer ShadowTransaction/ShadowPosting only used by the repo."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _illegal_storage_model_imports(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct imports of storage-layer ShadowTransaction / ShadowPosting "
        "outside the ShadowTransactionRepository — route through the repo "
        "so the balance trigger and the main↔shadow pairing stay correct:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
