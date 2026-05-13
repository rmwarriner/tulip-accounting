"""Schemas for ``GET /v1/system/diagnostics`` (#135).

The doctor CLI (``tulip doctor``) consumes this shape. Field semantics
are stable; new diagnostic fields may be added but never removed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SystemDiagnosticsRead(BaseModel):
    """Health-plus-configuration probe used by ``tulip doctor``."""

    alembic_head_in_db: str | None = Field(
        description=(
            "Current alembic revision stamped in the database, or ``null`` "
            "if the alembic_version table is missing (un-migrated DB)."
        )
    )
    alembic_head_expected: str = Field(
        description=("Head revision of the migration scripts bundled with the running API package.")
    )
    alembic_head_match: bool = Field(
        description=(
            "True iff the DB is at the head this build expects. False ⇒ run "
            "``alembic upgrade head`` against the deployed database."
        )
    )
    master_key_source: Literal["env", "file", "ephemeral"] = Field(
        description=(
            "Which env path produced the master key. ``ephemeral`` means no "
            "key was configured and a per-process random key is in use — "
            "field-encrypted columns will not survive a restart."
        )
    )
    master_key_loaded: bool = Field(
        description=(
            "True iff a non-ephemeral master key is in use (i.e. "
            "``master_key_source != 'ephemeral'``). Doctored as a hard "
            "failure on the CLI side."
        )
    )
    jwt_secret_source: Literal["env", "ephemeral"] = Field(
        description=(
            "Which env path produced the JWT signing secret. ``ephemeral`` "
            "means TULIP_JWT_SECRET was unset — every restart invalidates "
            "all outstanding access tokens. Added in #223."
        )
    )
    deployment_mode: Literal["dev", "prod"] = Field(
        description=(
            "Value of ``TULIP_ENV``. ``prod`` refuses to boot with any "
            "ephemeral secret; ``dev`` warns but allows. Added in #223."
        )
    )
    attachment_root_writable: bool = Field(
        description=(
            "True iff the configured ``attachment_root`` accepted a probe "
            "create + delete at request time."
        )
    )
