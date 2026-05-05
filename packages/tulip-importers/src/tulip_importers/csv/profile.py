"""CsvProfile — column-mapping configuration for the CSV importer (P5.2.c).

Per ADR-0004 §Q8. CSV files don't have a self-describing schema; each
bank emits its own column set. A `CsvProfile` is the user-supplied
mapping from "column name in this bank's CSV" to "field in
``ParsedStatementLine``". Profiles are stored in the DB
(``csv_profiles`` table from P5.1) and exported as YAML for sharing.

This module owns:

- :class:`CsvProfile` — the Pydantic model (single source of truth for
  the profile shape; the API's :class:`tulip_api.schemas.csv_profile.CsvProfileCreate`
  schema reuses this directly).
- :func:`CsvProfile.to_yaml` / :meth:`CsvProfile.from_yaml` — round-trip
  through YAML using ``yaml.safe_load`` only. ``yaml.load`` is banned by
  the architecture test in ``tulip-storage/tests/test_architecture_no_unsafe_yaml.py``.
"""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CsvProfile(BaseModel):
    """Per-bank column mapping for a CSV statement.

    Required fields: ``name``, ``date_column``, ``date_format``,
    ``amount_column``, ``description_column``. Everything else has a
    sensible default. Extra fields in the YAML body are ignored at
    construction time (forward-compatibility).
    """

    model_config = ConfigDict(
        extra="ignore",
        # The on-the-wire JSON API uses strict mode; the YAML codec
        # below opts back to lax via from_yaml (PyYAML returns plain
        # dicts that strict-mode would reject for fields like
        # skip_header_rows when given int strings from hand-edited YAML).
    )

    name: str = Field(min_length=1, max_length=100)
    date_column: str = Field(min_length=1)
    date_format: str = Field(
        min_length=1,
        description=(
            "strftime/strptime format. No presets — the user picked the "
            "bank's format on purpose. Common examples: '%m/%d/%Y' (US "
            "4-digit), '%Y-%m-%d' (ISO)."
        ),
    )
    amount_column: str = Field(min_length=1)
    amount_negative_means: Literal["debit", "credit"] = Field(
        default="debit",
        description=(
            "How the bank encodes signs. 'debit' (default): negative = "
            "money out, matches ParsedStatementLine.amount sign convention. "
            "'credit': bank prints expenses as positive (the credit-card "
            "convention); the parser flips signs."
        ),
    )
    description_column: str = Field(min_length=1)
    reference_column: str | None = Field(default=None)
    counterparty_column: str | None = Field(default=None)
    encoding: str = Field(default="utf-8", min_length=1)
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    skip_header_rows: int = Field(default=1, ge=0)

    def to_yaml(self) -> str:
        """Serialize to a human-readable YAML block."""
        data: dict[str, Any] = self.model_dump(mode="json", exclude_none=False)
        # default_flow_style=False forces block style (line per field), the
        # readable form. sort_keys=False preserves field declaration order
        # so exports are stable across Pydantic versions.
        return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> CsvProfile:
        """Parse a YAML block via ``yaml.safe_load`` and validate the model.

        Raises:
            yaml.YAMLError: payload isn't valid YAML, or contains unsafe
                tags (``!!python/object`` and friends).
            pydantic.ValidationError: the parsed dict fails schema
                validation.

        """
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"YAML must deserialize to a mapping, got {type(loaded).__name__}")
        return cls.model_validate(loaded)
