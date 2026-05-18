"""Architecture: capability modules contain no inline redaction heuristics (#347, M-8).

Audit privacy M-8 documented per-capability redactors (``nl_query._redact_description``,
``forecast._round_to_bucket``, proposals name-elision) reimplementing the
``PromptRedactor`` heuristic with their own constants. Drift risk: raising
``_KEEP_MIN_LEN`` in ``redaction.py`` wouldn't follow in ``nl_query.py``.

This test guards the consolidation by AST-scanning each capability module
for ``_redact_*`` / ``_bucket_*`` / ``_round_to_bucket`` private functions
that look like they re-implement the centralised heuristics. The
allowlist is tight: capabilities should call ``PromptRedactor`` methods,
not invent their own.

Adding a legitimate new private helper that *isn't* a redaction
re-implementation? Add it to the allowlist below with a comment
explaining why.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

#: Capability modules that must NOT carry inline redaction logic.
_CAPABILITY_FILES: Final[tuple[Path, ...]] = (
    Path(__file__).resolve().parents[1] / "src" / "tulip_ai" / "categorize.py",
    Path(__file__).resolve().parents[1] / "src" / "tulip_ai" / "nl_query.py",
    Path(__file__).resolve().parents[1] / "src" / "tulip_ai" / "proposals.py",
    # forecast.py is the historic home of bucket_time_series; the
    # module-level wrapper now delegates to PromptRedactor. Allow
    # ``bucket_time_series`` here as an explicit back-compat shim.
    Path(__file__).resolve().parents[1] / "src" / "tulip_ai" / "forecast.py",
)

#: Function-name prefixes/exact-names that look like redaction
#: heuristics being re-implemented locally. A capability file that
#: defines one of these (other than the allowlist below) fails the test.
_BANNED_PREFIXES: Final[tuple[str, ...]] = (
    "_redact_",
    "_round_to_bucket",
    "_strict_",
    "_bucket_amount",
)

#: Per-file allowlist of names that pattern-match the banned prefixes
#: but are deliberately kept here. Keep empty unless there's a strong
#: reason — the whole point is to push these into ``redaction.py``.
_ALLOWLIST: Final[dict[Path, frozenset[str]]] = {
    # forecast.py exports the public ``bucket_time_series`` shim that
    # delegates into PromptRedactor; the shim itself isn't a re-impl.
    # No private redact_*/bucket_* helpers should remain here.
}


def _find_inline_redactors(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` for every banned function defined in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            name = node.name
            allow = _ALLOWLIST.get(path, frozenset())
            if name in allow:
                continue
            for prefix in _BANNED_PREFIXES:
                if name.startswith(prefix):
                    hits.append((node.lineno, name))
                    break
    return hits


def test_capability_modules_have_no_inline_redaction() -> None:
    """Capability files MUST delegate redaction to PromptRedactor."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _CAPABILITY_FILES:
        hits = _find_inline_redactors(path)
        if hits:
            offenders[path.name] = hits

    assert not offenders, (
        "Capability modules re-implementing redaction heuristics locally — "
        "move them into PromptRedactor (see #347, audit M-8):\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
