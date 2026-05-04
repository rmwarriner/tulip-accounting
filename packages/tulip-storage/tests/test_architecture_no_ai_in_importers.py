"""Architecture test: ``tulip_importers`` may not import ``tulip_ai`` (P5.1).

Per ADR-0004 §"Module layout (tulip-importers)" + §"Architecture tests".
The auto-categorization seam lands in P5.3 as a `Categorizer` Protocol +
`register_categorizer` DI hook in `tulip_core.reconciliation`. Phase 6
plugs in an `AICategorizer` via the hook — never via direct import from
the importer modules.

`tulip-importers` doesn't exist yet (lands in P5.2.a/b/c). This test is
forward-looking: it trivially passes today (zero source files match) and
prevents regression once the package ships.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_IMPORTERS_SRC: Final[Path] = _REPO_ROOT / "packages" / "tulip-importers" / "src"

_BANNED_PREFIX: Final[str] = "tulip_ai"


def _python_source_files() -> list[Path]:
    if not _IMPORTERS_SRC.is_dir():
        return []  # tulip-importers package not yet created (P5.2)
    return sorted(_IMPORTERS_SRC.rglob("*.py"))


def _illegal_ai_imports(path: Path) -> list[tuple[int, str]]:
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


def test_no_tulip_ai_import_in_tulip_importers() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        hits = _illegal_ai_imports(path)
        if hits:
            offenders[str(path)] = hits

    assert not offenders, (
        "tulip_importers must not import tulip_ai directly — Phase 6 plugs "
        "in the AICategorizer via tulip_core.reconciliation.register_categorizer "
        "(per ADR-0004):\n" + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
