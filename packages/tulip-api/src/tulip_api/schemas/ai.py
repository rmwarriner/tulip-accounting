"""Schemas for ``/v1/ai/...`` endpoints (P6.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Security audit L-13 (#350): request schemas use extra="forbid". The
# two AIConfig*Patch schemas below already had it from the original
# P6.5.b implementation — those are kept; this PR brings the rest of
# the request bodies in this file into line.


class AIKeyCreate(BaseModel):
    """Body for ``POST /v1/ai/keys/{provider}``."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(
        min_length=1,
        description="The provider-issued API key. Stored field-encrypted.",
    )


class AIKeysList(BaseModel):
    """Response for ``GET /v1/ai/keys`` — names of providers that have keys configured."""

    providers: list[str]


class AIStatusRead(BaseModel):
    """Response for ``GET /v1/ai/status`` — resolved policy summary for the caller.

    P6.5.b extension: surfaces the cost-cap behaviour, rate limit, and
    fallback semantics so operators can see the contract before relying
    on it.
    """

    default_provider: str | None
    default_model: str | None
    monthly_cost_cap_usd: Decimal | None
    cost_cap_behaviour: Literal["degrade", "hard_fail"]
    rate_limit_per_hour: int
    fallback_provider: str | None
    fallback_model: str | None
    log_prompts: bool
    capabilities: dict[str, dict[str, str | None]]
    providers_with_keys: list[str]
    month_to_date_spend_usd: Decimal | None = None


class AIPreviewRequest(BaseModel):
    """Body for ``POST /v1/ai/preview`` — synthetic statement line for the categorize prompt."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    posted_date: date


class AIPreviewResponse(BaseModel):
    """The exact JSON body the live categorize call would send to the provider."""

    profile: Literal["default", "strict", "local_only"]
    provider: str | None
    model: str | None
    payload: dict[str, object]


class AICategorizeProposalsRequest(BaseModel):
    """Body for ``POST /v1/ai/categorize-proposals`` (#425)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    posted_date: date
    n: int = Field(default=5, ge=1, le=10)


class AICategorizeCandidate(BaseModel):
    """One ranked candidate in the propose response."""

    account_code: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class AICategorizeProposalsResponse(BaseModel):
    """Top-N candidates returned by the categorizer (#425)."""

    candidates: list[AICategorizeCandidate]


class AIAskRequest(BaseModel):
    """Body for ``POST /v1/ai/ask`` — one user question over the AI views (P6.2)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="Natural-language question; relayed to the model verbatim.",
    )


class AIAskResponse(BaseModel):
    """Result of the two-turn NL-query flow.

    ``rows`` are the unredacted query results so the user can verify the
    summary; the AI sees redacted rows on turn 2 (per ADR-0005 §Q3).
    ``error`` is populated when any step (provider, validation, execution)
    fails; ``summary`` is non-empty only on success.
    """

    summary: str
    rows: list[dict[str, object]]
    sql: str | None
    error: str | None = None


# --- P6.5.b: config editor ------------------------------------------------

# Sentinel string used in PATCH payloads to clear a field — distinct from
# ``None`` (omit / leave-as-is) and a real value. The Pydantic model below
# accepts ``None`` for "no change" and the sentinel ``"__CLEAR__"`` for
# "remove this key from ai_policy".
CLEAR_SENTINEL = "__CLEAR__"


class AIConfigCapability(BaseModel):
    """Per-capability override fields exposed on ``GET /v1/ai/config``."""

    policy: str | None
    provider: str | None
    model: str | None
    profile: str | None


class AIConfigRead(BaseModel):
    """Response for ``GET /v1/ai/config`` — household-level + per-capability state.

    Per-capability rows show only fields that have been overridden; the
    fully-resolved view lives at ``GET /v1/ai/status``.
    """

    default_provider: str | None
    default_model: str | None
    profile: str | None
    monthly_cost_cap_usd: Decimal | None
    cost_cap_behaviour: Literal["degrade", "hard_fail"]
    rate_limit_per_hour: int
    fallback_provider: str | None
    fallback_model: str | None
    log_prompts: bool
    #: Read-only. Non-proposal-linked ai_invocations older than this are
    #: GC'd by the ``ai_retention`` scheduled handler (#243).
    invocation_retention_days: int
    capabilities: dict[str, AIConfigCapability]


class AIConfigPatch(BaseModel):
    """Body for ``PUT /v1/ai/config`` — partial patch over ``households.ai_policy``.

    Field semantics:
    - ``None`` — leave the key unchanged.
    - A real value (string / Decimal / int / bool) — set the key.
    - The sentinel ``"__CLEAR__"`` (Decimal / int are special-cased via
      a string field below) — remove the key from ``ai_policy``.

    Unknown keys are rejected upstream by Pydantic's ``model_config``
    so the API surface stays tight.
    """

    model_config = {"extra": "forbid"}

    default_provider: str | None = None
    default_model: str | None = None
    profile: Literal["default", "strict", "local_only"] | None = None
    # Decimal / int come over the wire as strings so the sentinel can
    # share the field. Empty string or sentinel ⇒ clear.
    monthly_cost_cap_usd: str | None = None
    cost_cap_behaviour: Literal["degrade", "hard_fail"] | None = None
    rate_limit_per_hour: int | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    log_prompts: bool | None = None


class AIConfigCapabilityPatch(BaseModel):
    """Body for ``PUT /v1/ai/config/capabilities/{capability}`` — per-capability override.

    Fields use the same ``None`` / value / sentinel semantics as the
    household-level patch. Value-space validation (e.g. ``policy`` must be
    one of permissive / requires_approval / disabled) happens server-side
    so the ``__CLEAR__`` sentinel can share the field.
    """

    model_config = {"extra": "forbid"}

    policy: str | None = None
    provider: str | None = None
    model: str | None = None
    profile: str | None = None
