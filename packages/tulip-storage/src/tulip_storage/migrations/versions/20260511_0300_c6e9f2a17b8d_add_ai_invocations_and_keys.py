"""Add ai_invocations table, ai_policy/ai_keys columns (P6.1).

Per ADR-0005 §Q6, AI invocations get their own audit table — separate
from ``audit_log`` because every column on the row is AI-specific
(provider, model, tokens, cost, redaction profile, prompt hash). The
table is written exclusively by ``tulip_ai.audit.AIInvocationWriter``;
``test_architecture_no_direct_ai_invocation_writes.py`` enforces that
(lands in the tulip-ai test slice).

Plus three columns to support BYOK + per-tenant policy:

- ``households.ai_policy`` (JSON, nullable=False with default ``{}``) —
  the policy shape sketched in ARCHITECTURE.md §6.5; defaults to an
  empty object so existing households don't break on upgrade. The
  resolver treats empty/missing fields as "use code defaults"
  (capabilities default to ``permissive``, no provider, no cost cap).
- ``households.ai_keys_encrypted`` (LargeBinary, nullable) — encrypted
  JSON blob of provider→key. Encryption uses the same
  ``tulip_storage.encryption.encrypt_field`` flow as ``totp_secret_encrypted``.
- ``users.ai_keys_encrypted`` (LargeBinary, nullable) — same shape; per-user
  override of household keys.

Revision ID: c6e9f2a17b8d
Revises: a4f1c8d3e9b7
Create Date: 2026-05-11 03:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6e9f2a17b8d"
down_revision: str | None = "a4f1c8d3e9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ai_invocations + the three BYOK columns."""
    # ai_invocations — household-scoped audit rows, one per AI call (or per
    # preview-only run with outcome="redacted_only_preview").
    op.create_table(
        "ai_invocations",
        sa.Column("household_id", sa.CHAR(32), nullable=False),
        sa.Column("id", sa.CHAR(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actor_user_id", sa.CHAR(32), nullable=True),
        # capability: categorize / nl_query / forecast / agentic
        sa.Column("capability", sa.String(length=20), nullable=False),
        # policy_resolved: permissive / requires_approval / disabled
        sa.Column("policy_resolved", sa.String(length=30), nullable=False),
        # profile: default / strict / local_only
        sa.Column("profile", sa.String(length=20), nullable=False),
        # provider: anthropic / openai / google / ollama / openai-compatible / null (preview)
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "cost_estimate_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        # outcome: success / provider_error / redacted_only_preview /
        # policy_disabled / rate_limited / cost_capped
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("provider_response_id", sa.String(length=200), nullable=True),
        sa.Column("request_id", sa.CHAR(32), nullable=True),
        # SHA-256 of the redacted prompt payload (32 bytes). Always populated.
        sa.Column("prompt_hash", sa.LargeBinary(length=32), nullable=False),
        # Optional opt-in storage of the prompt and response — defaults NULL,
        # only populated when ``households.ai_policy.log_prompts`` is true.
        sa.Column("prompt_json", sa.Text, nullable=True),
        sa.Column("response_text", sa.Text, nullable=True),
        # FK to pending_proposals (lands P6.4). Nullable because most invocations
        # are not agentic; the table doesn't exist yet so no FK constraint.
        sa.Column("proposal_id", sa.CHAR(32), nullable=True),
        sa.PrimaryKeyConstraint("household_id", "id"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_ai_invocations_household_created",
        "ai_invocations",
        ["household_id", "created_at"],
    )
    op.create_index(
        "ix_ai_invocations_request_id",
        "ai_invocations",
        ["request_id"],
    )

    # households.ai_policy — JSON shape per ARCHITECTURE.md §6.5. SQLite stores
    # it as TEXT under the hood; the JSON type makes round-trips dict-shaped.
    # Default is an empty object: the resolver fills in defaults from code.
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_policy",
                sa.JSON,
                nullable=False,
                server_default="{}",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_keys_encrypted",
                sa.LargeBinary,
                nullable=True,
            )
        )

    # users.ai_keys_encrypted — per-user override of household keys.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_keys_encrypted",
                sa.LargeBinary,
                nullable=True,
            )
        )


def downgrade() -> None:
    """Drop ai_invocations + the three BYOK columns."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("ai_keys_encrypted")
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("ai_keys_encrypted")
        batch_op.drop_column("ai_policy")
    op.drop_index("ix_ai_invocations_request_id", table_name="ai_invocations")
    op.drop_index("ix_ai_invocations_household_created", table_name="ai_invocations")
    op.drop_table("ai_invocations")
