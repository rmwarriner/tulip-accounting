"""Add composite FK on pending_proposals.ai_invocation_id and notifications.ai_invocation_id.

Per #231 / privacy audit M-21. Previously the column was `CHAR(32)` with no
FK constraint, so a row in household A could in principle carry an
`ai_invocation_id` pointing at a row in household B. The schema-level
guarantee that composite FKs make cross-tenant references impossible
(ARCHITECTURE §3.3) didn't hold for these two columns.

Adds `(household_id, ai_invocation_id) → ai_invocations(household_id, id)`
ON DELETE SET NULL. SET-NULL preserves the proposal/notification row even
if the underlying invocation is purged; the audit chain weakens but the
business object survives.

Revision ID: b1c2d3e4f5a6
Revises: e8b5c2a9d1f4
Create Date: 2026-05-13 06:30:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "e8b5c2a9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite FKs on ai_invocation_id columns."""
    with op.batch_alter_table("pending_proposals") as batch:
        batch.create_foreign_key(
            "fk_pending_proposals_ai_invocation_id_ai_invocations",
            "ai_invocations",
            ["household_id", "ai_invocation_id"],
            ["household_id", "id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("notifications") as batch:
        batch.create_foreign_key(
            "fk_notifications_ai_invocation_id_ai_invocations",
            "ai_invocations",
            ["household_id", "ai_invocation_id"],
            ["household_id", "id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Drop the composite FKs."""
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint(
            "fk_notifications_ai_invocation_id_ai_invocations",
            type_="foreignkey",
        )
    with op.batch_alter_table("pending_proposals") as batch:
        batch.drop_constraint(
            "fk_pending_proposals_ai_invocation_id_ai_invocations",
            type_="foreignkey",
        )
