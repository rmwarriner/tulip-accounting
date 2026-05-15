"""Architecture test: every audit action string is tier-classified (#245).

The retention handler dispatches by ``audit_log.action`` via a static
``_RETENTION_TIER_BY_ACTION`` map. An action not in the map falls
through to ``default_days`` (90d safety net), which is conservative —
but a new action drifting into ``default_days`` is almost always a bug:

* A ledger mutation ageing at 90 days would lose tax-record continuity.
* An admin / lifecycle event ageing at 90 days would shrink the
  forensic window the audit's H-2 / H-14 / H-17 work depends on.

This test crawls every ``action="..."`` (or ``action=...`` keyword
literal) emitted by the API routers and asserts each one appears as a
key in ``_RETENTION_TIER_BY_ACTION``. Adding a new audit action without
adding a tier entry fails this test before the PR merges.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tulip_storage.runner.handlers.audit_retention import _RETENTION_TIER_BY_ACTION

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ROUTERS_DIR: Final[Path] = _REPO_ROOT / "packages" / "tulip-api" / "src" / "tulip_api" / "routers"


def _collect_actions(path: Path) -> set[str]:
    """Return every string-literal value passed as ``action=`` in this module."""
    actions: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return actions
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        actions.add(kw.value.value)
    return actions


def _all_router_actions() -> set[str]:
    out: set[str] = set()
    for path in sorted(_ROUTERS_DIR.rglob("*.py")):
        out |= _collect_actions(path)
    return out


def test_every_router_audit_action_is_tier_classified() -> None:
    """Every ``action="..."`` literal in the routers must appear in the tier map."""
    found = _all_router_actions()
    missing = sorted(found - set(_RETENTION_TIER_BY_ACTION))
    assert not missing, (
        f"Audit actions not tier-classified in _RETENTION_TIER_BY_ACTION: {missing}. "
        "Add explicit tier entries in "
        "packages/tulip-storage/src/tulip_storage/runner/handlers/audit_retention.py "
        "(or document why falling through to default_days is correct here)."
    )


def test_tier_map_is_non_empty_for_each_tier() -> None:
    """Defensive: each tier-key has at least one action mapped to it (except default_days)."""
    from tulip_storage.runner.handlers.audit_retention import _TIER_DEFAULTS

    mapped_tiers = set(_RETENTION_TIER_BY_ACTION.values())
    for tier in _TIER_DEFAULTS:
        if tier == "default_days":
            continue  # safety-net tier; explicit mappings are forbidden
        assert tier in mapped_tiers, f"tier {tier!r} has no actions mapped to it"
