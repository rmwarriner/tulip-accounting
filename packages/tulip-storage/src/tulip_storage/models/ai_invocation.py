"""``ai_invocations`` audit row — one per AI capability call (P6.1).

Per ADR-0005 §Q6. Written exclusively by ``tulip_ai.audit.AIInvocationWriter``;
``test_architecture_no_direct_ai_invocation_writes`` enforces that. The shape
mirrors ``audit_log`` (household-scoped, request-id correlatable) but the
columns are AI-specific so a single table is clearer than overloading
``audit_log.metadata_``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base, SqliteDecimal


class AICapability(Enum):
    """Capability the invocation targeted."""

    CATEGORIZE = "categorize"
    NL_QUERY = "nl_query"
    FORECAST = "forecast"
    AGENTIC = "agentic"


class AIOutcome(Enum):
    """How the invocation ended; drives reporting + cost-cap accounting."""

    SUCCESS = "success"
    PROVIDER_ERROR = "provider_error"
    REDACTED_ONLY_PREVIEW = "redacted_only_preview"
    POLICY_DISABLED = "policy_disabled"
    RATE_LIMITED = "rate_limited"
    COST_CAPPED = "cost_capped"


class AIInvocation(Base):
    """One row per AI call (or preview-only run)."""

    __tablename__ = "ai_invocations"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    capability: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_resolved: Mapped[str] = mapped_column(String(30), nullable=False)
    profile: Mapped[str] = mapped_column(String(20), nullable=False)
    # Provider/model are NULL on preview-only or policy-disabled rows.
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Decimal so the cost-cap arithmetic doesn't sprout floats.
    cost_estimate_usd: Mapped[Decimal] = mapped_column(
        SqliteDecimal(12, 6), nullable=False, default=Decimal("0")
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_response_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(CHAR(32), nullable=True)
    # SHA-256 of the redacted prompt payload. Always populated.
    prompt_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    # Optional opt-in storage of prompt + response. NULL unless
    # ``households.ai_policy.log_prompts == true``.
    prompt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to pending_proposals (P6.4); table doesn't exist yet so no constraint.
    proposal_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
