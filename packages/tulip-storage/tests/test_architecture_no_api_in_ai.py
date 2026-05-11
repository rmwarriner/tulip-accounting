"""Architecture test: ``tulip_ai`` may not import ``tulip_api`` (P6.1).

Per ADR-0005 §Q1. ``tulip-ai``'s dependency direction is ``tulip-core`` +
``tulip-storage`` only. HTTP endpoints (``POST /v1/ai/preview``,
``POST /v1/ai/ask``) live in ``tulip-api`` and call *into* ``tulip-ai``,
never the reverse. This mirrors the same one-direction rule
``tulip-importers`` follows for ``tulip-api`` and that ``tulip-core``
follows for everyone.

The test pattern (AST walk, per-file violations dict, helpful error
message) mirrors ``test_architecture_no_ai_in_importers.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AI_SRC: Final[Path] = _REPO_ROOT / "packages" / "tulip-ai" / "src"

_BANNED_PREFIX: Final[str] = "tulip_api"


def _python_source_files() -> list[Path]:
    if not _AI_SRC.is_dir():
        return []
    return sorted(_AI_SRC.rglob("*.py"))


def _illegal_api_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == _BANNED_PREFIX or node.module.startswith(_BANNED_PREFIX + ".")
            ):
                hits.append((node.lineno, f"from {node.module} import ..."))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _BANNED_PREFIX or alias.name.startswith(_BANNED_PREFIX + "."):
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def test_no_tulip_api_import_in_tulip_ai() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        hits = _illegal_api_imports(path)
        if hits:
            offenders[str(path)] = hits

    assert not offenders, (
        "tulip_ai must not import tulip_api — the dependency direction is "
        "core ← storage ← ai ← api (ADR-0005 §Q1). HTTP endpoints in tulip-api "
        "call into tulip-ai, never the reverse:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
