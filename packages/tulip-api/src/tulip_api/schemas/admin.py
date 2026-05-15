"""Admin / operator schemas — audit-log retention policy (#245)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditRetentionPolicyRead(BaseModel):
    """Resolved per-tier audit-log retention for the caller's household (#245).

    Each value is the effective day-count — the operator's override
    (from ``households.audit_retention_policy``) merged with the code
    default from
    ``tulip_storage.runner.handlers.audit_retention._TIER_DEFAULTS``.
    A fresh household reads back the code defaults verbatim.
    """

    ledger_days: int = Field(
        description=(
            "Retention for ledger-mutation rows (create / update / delete / "
            "void / description_rectified / reconciliation_* / etc.). "
            "Default 2555 days (~7 years, US tax-record anchored)."
        )
    )
    auth_days: int = Field(
        description=(
            "Retention for auth events (login / refresh / MFA enrollment / "
            "password change). Default 90 days."
        )
    )
    ai_days: int = Field(
        description=("Retention for AI capability + consent events. Default 30 days.")
    )
    admin_days: int = Field(
        description=(
            "Retention for admin lifecycle events (user.deleted / "
            "household.* / csv_profile.* / audit.pruned summaries). "
            "Default 365 days."
        )
    )
    default_days: int = Field(
        description=(
            "Safety-net retention for any audit action not explicitly tiered. Default 90 days."
        )
    )


class AuditRetentionPolicyPatch(BaseModel):
    """PATCH body for ``PUT /v1/admin/audit-policy`` (#245).

    All five tiers optional. Sending ``null`` or omitting a key resets
    that tier to its code default. Positive integers only — a typo
    (zero, negative, or non-int) is also resolved to the code default
    at handler-run time, but the schema validator rejects them so the
    operator gets a 422 instead of silent fallback.
    """

    model_config = ConfigDict(extra="forbid")

    ledger_days: int | None = Field(default=None, gt=0)
    auth_days: int | None = Field(default=None, gt=0)
    ai_days: int | None = Field(default=None, gt=0)
    admin_days: int | None = Field(default=None, gt=0)
    default_days: int | None = Field(default=None, gt=0)


class AuditPruneResult(BaseModel):
    """Response shape for ``POST /v1/admin/audit-prune`` (#245).

    ``deleted_per_tier`` mirrors what the daily handler logs — a
    per-tier integer count of rows removed by this run.
    """

    household_id: UUID
    deleted_per_tier: dict[str, int]
    total_deleted: int
