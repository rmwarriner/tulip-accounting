"""Architecture test: no source file references FastAPI's HTTPException.

After P2.x.2 every error path raises a :class:`tulip_api.errors.TulipProblem`
subclass and is rendered as RFC 9457 ``application/problem+json``. Plain
``HTTPException`` is therefore forbidden in ``tulip_api/src/`` —
re-introducing it (e.g. via a copy-paste stub or AI-suggested code) would
break the contract clients depend on.

This test scans the source tree for any reference to ``HTTPException``,
whether imported, raised, or used as a type annotation. The assertion is
strict: zero occurrences.
"""

from __future__ import annotations

import ast
from pathlib import Path

_API_SRC = Path(__file__).resolve().parents[1] / "src" / "tulip_api"


def _python_files() -> list[Path]:
    return sorted(_API_SRC.rglob("*.py"))


def _references_http_exception(path: Path) -> list[int]:
    """Return line numbers in ``path`` that mention ``HTTPException`` as code.

    Strings, comments, and docstrings are ignored — only AST ``Name``
    nodes count. ``ImportFrom`` is checked separately because its alias
    list isn't visited as ``Name`` nodes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "HTTPException":
                    hits.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(".HTTPException"):
                    hits.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "HTTPException":
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "HTTPException":
            hits.append(node.lineno)
    return hits


def test_no_http_exception_in_source() -> None:
    """Every error path must use TulipProblem, not HTTPException."""
    offenders: dict[str, list[int]] = {}
    for path in _python_files():
        hits = _references_http_exception(path)
        if hits:
            offenders[str(path.relative_to(_API_SRC))] = hits

    assert not offenders, (
        "HTTPException references found in tulip_api source — migrate to "
        "TulipProblem subclasses in tulip_api/errors.py:\n"
        + "\n".join(f"  {file}: lines {lines}" for file, lines in offenders.items())
    )
