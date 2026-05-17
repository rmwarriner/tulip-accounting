"""Architecture test: ``AuditLog`` rows are written only via ``AuditLogWriter``.

Per security audit M-18 (#331): the writer is the contract chokepoint for
the audit_log table — it stamps ``request_id``, validates ``household_id``,
and normalises ``occurred_at``. A future contributor (or an AI-generated
patch) could instantiate ``AuditLog(...)`` directly to "skip the writer
for a special case" and bypass those invariants. This test catches the
regression.

The check is constructor-call-based, not import-based: legitimate read
paths (the audit-retention prune handler, the audit-log report) import
``AuditLog`` for ``select`` / ``delete`` queries. Those are not flagged.
Only ``AuditLog(...)`` calls — which can only mean "construct a new row" —
are.

Mirrors the eleven other ``test_architecture_no_direct_X_writes.py``
tests in shape; replaces the missing ``audit_log`` entry in that family.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

#: Files allowed to construct ``AuditLog(...)`` directly.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # The model file itself declares the class.
        "tulip-storage/src/tulip_storage/models/audit_log.py",
        # The single legitimate writer — the chokepoint this test
        # defends.
        "tulip-storage/src/tulip_storage/repositories/audit_log.py",
    }
)


def _python_source_files() -> list[Path]:
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _audit_log_constructions(path: Path) -> list[int]:
    """Return line numbers of ``AuditLog(...)`` constructor calls in ``path``.

    Matches both ``AuditLog(...)`` (after ``from tulip_storage.models import
    AuditLog``) and ``models.AuditLog(...)`` / ``tulip_storage.models.AuditLog(...)``.
    Does not match ``AuditLog.foo``, ``AuditLog.action``, etc. — only Call nodes
    where the callee resolves to ``AuditLog``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "AuditLog":
            hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "AuditLog":
            hits.append(node.lineno)
    return hits


def test_no_direct_audit_log_construction_outside_allowlist() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if rel in _ALLOWED_RELATIVE:
            continue
        hits = _audit_log_constructions(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct ``AuditLog(...)`` construction outside the AuditLogWriter "
        "chokepoint — route writes through ``AuditLogWriter(session, "
        "household_id).write(...)`` so request_id / household_id / "
        "occurred_at invariants stay correct:\n"
        + "\n".join(f"  {file}:{lines}" for file, lines in offenders.items())
    )
