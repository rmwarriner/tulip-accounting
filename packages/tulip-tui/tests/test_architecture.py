"""Architecture boundary tests for tulip-tui.

ARCHITECTURE.md §9 + ADR-0007: the TUI is a network client of the API.
It must not import server-side or storage internals — talking to the API
is the only allowed channel, identical to the rule ``tulip-cli`` lives
under. Reuse of ``tulip-cli``'s HTTP client and token store (per
[#309](https://github.com/rmwarriner/tulip-accounting/issues/309))
is permitted; ``tulip_cli`` is therefore not in the forbidden set.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_FORBIDDEN_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {
        "tulip_api",
        "tulip_storage",
        "tulip_ai",
        "tulip_importers",
        "tulip_reports",
        "sqlalchemy",
        "alembic",
        "fastapi",
        "starlette",
        "uvicorn",
    }
)

TUI_SRC: Final[Path] = Path(__file__).resolve().parents[1] / "src" / "tulip_tui"


def _iter_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_tulip_tui_has_no_forbidden_imports() -> None:
    violations: dict[Path, set[str]] = {}
    for py_file in TUI_SRC.rglob("*.py"):
        bad = _iter_imports(py_file) & _FORBIDDEN_TOP_LEVEL
        if bad:
            violations[py_file] = bad
    assert not violations, (
        "tulip-tui leaked imports across an architectural boundary: "
        f"{ {str(p): sorted(v) for p, v in violations.items()} }"
    )


def test_tui_src_directory_exists() -> None:
    assert TUI_SRC.is_dir()
    assert any(TUI_SRC.rglob("*.py"))
