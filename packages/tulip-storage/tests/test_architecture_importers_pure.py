"""Architecture test: tulip_importers stays pure (P5.2.a).

Per ADR-0004 §"Module layout (tulip-importers)", importers depend only
on ``tulip-core`` (for ``ParsedStatementLine``) and stdlib + per-format
parsing libraries (e.g., ``ofxtools``). They must not import:

- Storage-layer types: ``tulip_storage`` (would break the "pure parser"
  contract; the API materializes ``StatementLine`` after the importer
  returns).
- API-layer types: ``tulip_api`` (no HTTP in importers).
- Heavy frameworks: ``sqlalchemy``, ``alembic``, ``fastapi``, ``starlette``,
  ``httpx``, ``typer``, ``click``.

The sibling ``test_architecture_no_ai_in_importers.py`` separately bans
``tulip_ai`` (the categorization seam plugs in via
``tulip_core.reconciliation.register_categorizer`` in P5.3, never via
direct import).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_IMPORTERS_SRC: Final[Path] = _REPO_ROOT / "packages" / "tulip-importers" / "src"

_BANNED_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        "tulip_storage",
        "tulip_api",
        "tulip_cli",
        "tulip_reports",
        "sqlalchemy",
        "alembic",
        "fastapi",
        "starlette",
        "uvicorn",
        "httpx",
        "typer",
        "click",
    }
)


def _python_source_files() -> list[Path]:
    if not _IMPORTERS_SRC.is_dir():
        return []
    return sorted(_IMPORTERS_SRC.rglob("*.py"))


def _illegal_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in _BANNED_PREFIXES:
                hits.append((node.lineno, f"from {node.module} import ..."))
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BANNED_PREFIXES:
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def test_tulip_importers_imports_no_io_layers() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        hits = _illegal_imports(path)
        if hits:
            offenders[str(path)] = hits

    assert not offenders, (
        "tulip_importers must not import storage-layer, API-layer, or "
        "framework types — parsers consume bytes and return "
        "ParsedStatementLine; the API materializes the persisted form "
        "(per ADR-0004 §Module layout):\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
