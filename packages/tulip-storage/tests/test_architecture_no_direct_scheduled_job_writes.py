"""Architecture test: scheduled_jobs writes go through the runner.

Per ADR-0002 §3 the scheduler is the single chokepoint for inserting
``scheduled_jobs`` rows. Direct construction of the ORM model anywhere
else (a router, a CLI handler, a future migration helper) would let
callers bypass the idempotency-key validation, the dtstart-anchoring
contract, and the eventual multi-worker safety story.

This test mirrors :mod:`test_architecture_no_direct_shadow_writes` —
AST-scans all ``packages/*/src/`` files for imports of the storage-layer
``ScheduledJob`` model and rejects any not in the allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

_BANNED_NAMES: Final[frozenset[str]] = frozenset({"ScheduledJob", "ScheduledJobRun"})

#: Modules whose ``ScheduledJob``/``ScheduledJobRun`` are the storage-layer ORM models.
_STORAGE_MODEL_MODULES: Final[frozenset[str]] = frozenset(
    {
        "tulip_storage.models",
        "tulip_storage.models.scheduled_job",
    }
)

#: Files allowed to import the storage-layer model classes.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # Model definitions.
        "tulip-storage/src/tulip_storage/models/scheduled_job.py",
        # Re-exports.
        "tulip-storage/src/tulip_storage/models/__init__.py",
        # The runner — only legitimate writer.
        "tulip-storage/src/tulip_storage/runner/runner.py",
        "tulip-storage/src/tulip_storage/runner/__init__.py",
        # First-party handlers — receive ScheduledJob instances from the
        # runner; the type-only import is required for typing.
        "tulip-storage/src/tulip_storage/runner/handlers/envelope_refill.py",
        "tulip-storage/src/tulip_storage/runner/handlers/daily_insights.py",
        # Read-only repository for the API to query schedules. Writes
        # still go through the Runner (the architecture test's actual
        # invariant). The class doesn't construct or mutate ScheduledJob
        # rows — only selects.
        "tulip-storage/src/tulip_storage/repositories/scheduled_job.py",
        # API router for refill schedules — TYPE_CHECKING-only import for
        # the helper function's annotation. Doesn't construct rows;
        # writes still route through the Runner via runner.schedule_*.
        "tulip-api/src/tulip_api/routers/refill_schedules.py",
    }
)


def _python_source_files() -> list[Path]:
    """Yield every .py file under ``packages/*/src/``."""
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _illegal_storage_model_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` pairs that import storage-layer ScheduledJob models."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _STORAGE_MODEL_MODULES:
            for alias in node.names:
                if alias.name in _BANNED_NAMES:
                    hits.append((node.lineno, alias.name))
    return hits


def test_no_direct_scheduled_job_writes_outside_runner() -> None:
    """Every scheduler write must route through the Runner."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _illegal_storage_model_imports(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct imports of storage-layer ScheduledJob / ScheduledJobRun "
        "outside the Runner — route through tulip_storage.runner.Runner so "
        "the idempotency / dtstart / retry contract stays intact:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
