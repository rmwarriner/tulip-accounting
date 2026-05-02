"""Envelope API schemas.

Mirrors the structure of :mod:`tulip_api.schemas.account` — a Create body, a
PATCH body with all-optional fields, and a Read response. The nested
``RefillRule`` is accepted as a structured JSON object whose shape matches
:meth:`tulip_core.allocation.RefillRule.to_dict` — never an expression
string. The router converts via ``RefillRule.from_dict`` at the boundary so
the no-eval guarantee from the threat-model checkpoint stays intact.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class RefillRuleSchema(BaseModel):
    """Structured shape for an envelope's optional refill rule.

    Three valid combinations:
    - ``strategy="fixed_amount"`` + ``amount`` + ``currency``.
    - ``strategy="fill_to_amount"`` + ``amount`` + ``currency``.
    - ``strategy="percentage_of_income"`` + ``percentage`` (0 < p ≤ 1).

    Cross-field validation is done in
    :meth:`tulip_core.allocation.RefillRule.__post_init__`; the schema only
    checks gross shape so user errors surface at the engine layer with the
    final error wording.
    """

    strategy: str = Field(
        pattern=r"^(fixed_amount|fill_to_amount|percentage_of_income)$",
    )
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    percentage: Decimal | None = None

    @model_validator(mode="after")
    def _check_amount_currency_paired(self) -> RefillRuleSchema:
        # If amount or currency is set, the other must be too. Defer the
        # per-strategy semantics to the domain layer.
        if (self.amount is None) != (self.currency is None):
            msg = "amount and currency must both be set or both be None"
            raise ValueError(msg)
        return self


class EnvelopeCreate(BaseModel):
    """Body for ``POST /v1/envelopes``."""

    name: str = Field(min_length=1, max_length=200)
    currency: str = Field(min_length=3, max_length=3)
    budget_period: str = Field(
        pattern=r"^(weekly|biweekly|monthly|quarterly|annual|custom)$",
    )
    rollover_policy: str = Field(pattern=r"^(reset|accumulate|cap_at_budget)$")
    budget_amount: Decimal | None = Field(default=None, ge=0)
    refill_rule: RefillRuleSchema | None = None
    visibility: str = Field(default="shared", pattern=r"^(shared|private)$")


class EnvelopeUpdate(BaseModel):
    """Body for ``PATCH /v1/envelopes/{id}``. Each field is optional.

    Currency is immutable (would invalidate every shadow posting on the
    pool). ``is_active`` is managed via DELETE; do not expose here.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    visibility: str | None = Field(default=None, pattern=r"^(shared|private)$")
    budget_period: str | None = Field(
        default=None,
        pattern=r"^(weekly|biweekly|monthly|quarterly|annual|custom)$",
    )
    budget_amount: Decimal | None = Field(default=None, ge=0)
    rollover_policy: str | None = Field(
        default=None,
        pattern=r"^(reset|accumulate|cap_at_budget)$",
    )
    refill_rule: RefillRuleSchema | None = None


class EnvelopeRead(BaseModel):
    """Response shape for ``GET /v1/envelopes`` and friends."""

    id: UUID
    name: str
    currency: str
    visibility: str
    is_active: bool
    budget_period: str
    rollover_policy: str
    budget_amount: Decimal | None
    refill_rule: RefillRuleSchema | None


class RefillRequest(BaseModel):
    """Body for ``POST /v1/envelopes/{id}/refill``.

    Source is implicitly the household's ``Unallocated`` system pool of the
    envelope's currency (lazy-created if missing). v1 is permissive on
    pushing Unallocated negative — that's intent, not a money invariant.
    """

    amount: Decimal = Field(gt=0)
    date: date_type
    description: str = Field(min_length=1, max_length=500)
    memo: str | None = Field(default=None, max_length=500)
