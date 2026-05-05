"""API schemas for CSV profiles (P5.2.c).

The validation surface piggybacks on
:class:`tulip_importers.csv.CsvProfile` — that's the single source of
truth for the profile shape; the API layer is just the JSON façade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tulip_importers.csv import CsvProfile


class CsvProfileCreate(CsvProfile):
    """Body for ``POST /v1/imports/profiles``.

    Identical to :class:`tulip_importers.csv.CsvProfile`; the subclass
    exists only so OpenAPI lists the schema under a request-side name.
    """

    model_config = ConfigDict(extra="forbid")


class CsvProfileUpdate(BaseModel):
    """Body for ``PATCH /v1/imports/profiles/{id_or_name}``.

    Every field optional. Fields omitted from the body keep their
    current value. A no-op patch (empty body) is a no-op response.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    date_column: str | None = Field(default=None, min_length=1)
    date_format: str | None = Field(default=None, min_length=1)
    amount_column: str | None = Field(default=None, min_length=1)
    amount_negative_means: Literal["debit", "credit"] | None = None
    description_column: str | None = Field(default=None, min_length=1)
    reference_column: str | None = None
    counterparty_column: str | None = None
    encoding: str | None = Field(default=None, min_length=1)
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    skip_header_rows: int | None = Field(default=None, ge=0)


class CsvProfileRead(CsvProfile):
    """Response shape for CSV-profile endpoints.

    Inherits all profile fields and adds persistence metadata.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    created_at: datetime
    updated_at: datetime
