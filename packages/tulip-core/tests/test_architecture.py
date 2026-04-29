"""Architecture boundary tests for tulip-core.

ARCHITECTURE.md §9 mandates: tulip-core is the pure-domain layer; it must
not import any I/O package or any sibling workspace package. Walking the
source tree and parsing each module's imports keeps the boundary honest
even as the codebase grows.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

# Roots of any import the core layer must NOT reach into.
_FORBIDDEN_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {
        # Sibling workspace packages — the dependency would invert the
        # architecture (storage and API depend on core, never vice versa).
        "tulip_storage",
        "tulip_api",
        "tulip_ai",
        "tulip_importers",
        "tulip_reports",
        "tulip_cli",
        # I/O frameworks — none of these belong in pure domain code.
        "sqlalchemy",
        "alembic",
        "fastapi",
        "starlette",
        "uvicorn",
        "httpx",
        "litellm",
        "anthropic",
        "openai",
        "typer",
        "click",
        "weasyprint",
        "jinja2",
        # Standard-library I/O the core should not need either.
        "sqlite3",
        "socket",
        "requests",
        "urllib3",
        "boto3",
    }
)

CORE_SRC: Final[Path] = Path(__file__).resolve().parents[1] / "src" / "tulip_core"


def _iter_imports(path: Path) -> set[str]:
    """Return the set of top-level modules imported by the python file at `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_tulip_core_has_no_forbidden_imports() -> None:
    """No forbidden top-level imports anywhere in packages/tulip-core/src."""
    violations: dict[Path, set[str]] = {}
    for py_file in CORE_SRC.rglob("*.py"):
        imported = _iter_imports(py_file)
        bad = imported & _FORBIDDEN_TOP_LEVEL
        if bad:
            violations[py_file] = bad
    assert not violations, (
        "tulip-core leaked imports across an architectural boundary: "
        f"{ {str(p): sorted(v) for p, v in violations.items()} }"
    )


def test_core_src_directory_exists() -> None:
    """Sanity-check the search root so an accidental rename can't silently pass."""
    assert CORE_SRC.is_dir()
    assert any(CORE_SRC.rglob("*.py"))
