"""Architecture test: reads of household-scoped models route through repositories.

Per security audit M-15 (#332): the repository pattern is the
chokepoint that guarantees tenant scoping and audit-log discipline.
Existing arch tests cover direct *writes* to chokepointed tables;
this one covers *reads* — any ``select(<HouseholdScopedModel>)``
outside the repositories module (or an explicit allowlist) is a
potential cross-tenant disclosure if the caller forgets the
``household_id`` filter.

We don't try to validate the WHERE clause itself (too brittle to AST-
walk reliably); the chokepoint is "go through the repo," and the
repo enforces the household_id filter in its constructor. The
allowlist captures the deliberate exceptions (auth pre-tenant flow,
the AI categorize path, the reports package, etc.).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PACKAGES: Final[Path] = _REPO_ROOT / "packages"

#: Household-scoped model classes. Reads of these via ``select(Model)``
#: must route through a repository (or be allowlisted). Sourced from a
#: grep for ``household_id: Mapped[UUID]`` across the models package
#: at the time the test was written; extending the set requires
#: explicit thought about whether the new model is tenant-scoped.
_HOUSEHOLD_SCOPED_MODELS: Final[frozenset[str]] = frozenset(
    {
        "Account",
        "AIInvocation",
        "AllocationPool",
        "Attachment",
        "AttachmentLink",
        "AuditLog",
        "CsvProfile",
        "Envelope",
        "ImportBatch",
        "MfaRecoveryCode",
        "Notification",
        "PendingHouseholdErasure",
        "PendingProposal",
        "Period",
        "Posting",
        "Reconciliation",
        "ReconciliationMatch",
        "ScheduledJob",
        "ScheduledJobRun",
        "Session",
        "ShadowPosting",
        "ShadowTransaction",
        "SinkingFund",
        "StatementLine",
        "Transaction",
        "UsedMfaChallenge",
        "User",
    }
)

#: Files allowed to call ``select(<HouseholdScopedModel>)`` directly.
#: Every entry is a relative path under ``packages/`` and carries an
#: inline justification.
_ALLOWED_RELATIVE: Final[frozenset[str]] = frozenset(
    {
        # All repository files — they ARE the chokepoint.
        # (Listed via the directory-prefix check in
        # ``_is_allowed_path`` below, but enumerated here for grep-ability.)
        # Auth pre-tenant flow: login / register operate before a
        # household_id is known. The dummy-verify timing-defense
        # (#221) reads the User table cross-tenant by email.
        "tulip-api/src/tulip_api/routers/auth.py",
        # GDPR Art. 15 data export (#241) selects every user-scoped
        # row for the subject's export envelope. Tenant-scoped via
        # claims.household_id at the WHERE clause.
        "tulip-api/src/tulip_api/routers/users.py",
        # AI router selects AIInvocation rows for the status surface;
        # all WHEREd by household_id (verified in #239).
        "tulip-api/src/tulip_api/routers/ai.py",
        # Household-erasure flow selects Attachment.content_hash
        # before the cascade so the post-delete file-unlink can
        # identify orphans. Scoped to claims.household_id.
        "tulip-api/src/tulip_api/routers/households.py",
        # Admin policy + grep-pii (#346) — admin-only endpoints
        # scoped via require_role("admin") + claims.household_id.
        "tulip-api/src/tulip_api/routers/admin.py",
        # Reconciliation matcher service — scoped via the recon row
        # which carries household_id.
        "tulip-api/src/tulip_api/services/reconciliation_match.py",
        # Import-apply service — selects transactions / postings
        # through the categorizer chain. Tenant-scoped via the
        # caller's household_id.
        "tulip-api/src/tulip_api/services/import_apply.py",
        # AI capability layer — selects households + invocations
        # scoped by the HouseholdContext (verified in P6.1 + #239).
        "tulip-ai/src/tulip_ai/categorize.py",
        # Reports package — every report function takes household_id
        # as a required arg and threads it through every SELECT
        # (architecture test for reports is the per-report unit-test
        # surface; this one allowlists the package directory).
        # (Covered by the directory prefix check.)
        # Storage runtime helpers + handlers — scoped per-household
        # loops (already arch-tested for direct writes; this entry
        # covers the read side).
        "tulip-storage/src/tulip_storage/grep_pii.py",
        "tulip-storage/src/tulip_storage/runner/runner.py",
    }
)

#: Directory prefixes whose every file is allowlisted.
_ALLOWED_PREFIXES: Final[tuple[str, ...]] = (
    "tulip-storage/src/tulip_storage/repositories/",
    "tulip-storage/src/tulip_storage/runner/handlers/",
    "tulip-reports/src/tulip_reports/",
)


def _python_source_files() -> list[Path]:
    out: list[Path] = []
    for pkg in sorted(_PACKAGES.iterdir()):
        src = pkg / "src"
        if not src.is_dir():
            continue
        out.extend(sorted(src.rglob("*.py")))
    return out


def _is_allowed_path(rel: str) -> bool:
    if rel in _ALLOWED_RELATIVE:
        return True
    return any(rel.startswith(p) for p in _ALLOWED_PREFIXES)


def _direct_select_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, model_name)`` pairs for ``select(<HouseholdScopedModel>)`` calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # ``select(<Name>)``  — bare function call.
        if isinstance(func, ast.Name) and func.id == "select":
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _HOUSEHOLD_SCOPED_MODELS:
                    hits.append((node.lineno, arg.id))
        # ``sqlalchemy.select(<Name>)`` / ``sa.select(<Name>)`` —
        # attribute call. The attr is "select"; the args are the same shape.
        elif isinstance(func, ast.Attribute) and func.attr == "select":
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _HOUSEHOLD_SCOPED_MODELS:
                    hits.append((node.lineno, arg.id))
    return hits


def test_no_direct_select_of_household_scoped_models_outside_allowlist() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_source_files():
        rel = str(path.relative_to(_PACKAGES))
        if _is_allowed_path(rel):
            continue
        hits = _direct_select_calls(path)
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "Direct ``select(<HouseholdScopedModel>)`` outside the repository "
        "chokepoint — route through the matching ``*Repository`` so the "
        "tenant scope is enforced by the constructor:\n"
        + "\n".join(f"  {file}: {hits}" for file, hits in offenders.items())
    )
