"""Schemas for ``/v1/ai/...`` endpoints (P6.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class AIKeyCreate(BaseModel):
    """Body for ``POST /v1/ai/keys/{provider}``."""

    api_key: str = Field(
        min_length=1,
        description="The provider-issued API key. Stored field-encrypted.",
    )


class AIKeysList(BaseModel):
    """Response for ``GET /v1/ai/keys`` — names of providers that have keys configured."""

    providers: list[str]


class AIStatusRead(BaseModel):
    """Response for ``GET /v1/ai/status`` — resolved policy summary for the caller."""

    default_provider: str | None
    default_model: str | None
    monthly_cost_cap_usd: Decimal | None
    log_prompts: bool
    capabilities: dict[str, dict[str, str | None]]
    providers_with_keys: list[str]


class AIPreviewRequest(BaseModel):
    """Body for ``POST /v1/ai/preview`` — synthetic statement line for the categorize prompt."""

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
