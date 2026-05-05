"""Architecture test: no unsafe ``yaml.load`` outside the allowlist (P5.2.c).

PyYAML's ``yaml.load`` invokes a Loader that historically allowed
arbitrary Python-object construction via ``!!python/object`` tags.
The safe path is ``yaml.safe_load``. This test scans every Python
source file under ``packages/*/src`` and rejects ``yaml.load`` calls
(plus the equally-dangerous ``full_load`` / ``unsafe_load`` variants).

Tests are intentionally not scanned — test code may need to construct
malicious YAML payloads to *prove* the production path rejects them.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"


def _python_source_files() -> list[Path]:
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _unsafe_yaml_load_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for any ``yaml.load(...)`` call.

    Matches the AST shape ``Call(func=Attribute(value=Name('yaml'), attr='load'))``.
    Variants (``yaml.full_load``, ``yaml.unsafe_load``) are also flagged
    since both can deserialize Python-object tags.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "yaml"
            and func.attr in {"load", "full_load", "unsafe_load"}
        ):
            hits.append((node.lineno, f"yaml.{func.attr}(...)"))
    return hits


def test_no_unsafe_yaml_load_in_production() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        hits = _unsafe_yaml_load_calls(path)
        if hits:
            offenders[str(path.relative_to(_PACKAGES))] = hits

    assert not offenders, (
        "yaml.load / yaml.full_load / yaml.unsafe_load call detected. Use "
        "yaml.safe_load — the unsafe variants deserialize "
        "!!python/object tags and are an arbitrary-code-execution sink:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
