"""Response schemas for resource lifecycle actions — deactivate + redact (#236)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeactivationResponse(BaseModel):
    """Honest body for a ``DELETE /v1/<resource>/{id}`` soft-delete.

    The DELETE verb *deactivates* (``is_active=False``) rather than hard-
    deleting — posting FKs are ``ON DELETE RESTRICT`` and rewriting ledger
    history just to drop a label is wrong. ``data_retained`` names the
    field types that survive the deactivation, so the caller knows a
    follow-up ``POST .../redact`` is needed to actually erase the PII.
    """

    action: Literal["deactivated"] = "deactivated"
    data_retained: list[str] = Field(
        description="Field types that still hold data after deactivation.",
    )


class RedactionResponse(BaseModel):
    """Body for a ``POST /v1/<resource>/{id}/redact`` action.

    Redaction nulls / placeholder-fills the PII columns on an already-
    deactivated row. Ledger postings keep their FK and amounts — history
    is preserved, the PII is gone.
    """

    action: Literal["redacted"] = "redacted"
    fields_redacted: list[str] = Field(
        description="Field types that were nulled or placeholder-filled.",
    )
