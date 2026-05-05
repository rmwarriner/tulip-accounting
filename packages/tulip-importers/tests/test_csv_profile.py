"""Tests for the CsvProfile value object + YAML codec (P5.2.c)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tulip_importers.csv import CsvProfile

_VALID = dict(
    name="chase-checking",
    date_column="Posting Date",
    date_format="%m/%d/%Y",
    amount_column="Amount",
    description_column="Description",
)


class TestConstruction:
    def test_minimal_required_fields(self):
        p = CsvProfile(**_VALID)
        assert p.name == "chase-checking"
        # Defaults pick up.
        assert p.amount_negative_means == "debit"
        assert p.encoding == "utf-8"
        assert p.delimiter == ","
        assert p.skip_header_rows == 1
        assert p.reference_column is None
        assert p.counterparty_column is None

    def test_all_fields_round_trip(self):
        p = CsvProfile(
            name="amex",
            date_column="Date",
            date_format="%Y-%m-%d",
            amount_column="Amount",
            amount_negative_means="credit",
            description_column="Description",
            reference_column="Reference",
            counterparty_column="Counterparty",
            encoding="latin-1",
            delimiter=";",
            skip_header_rows=2,
        )
        assert p.amount_negative_means == "credit"
        assert p.delimiter == ";"
        assert p.skip_header_rows == 2

    def test_missing_required_field_rejected(self):
        for missing in (
            "name",
            "date_column",
            "date_format",
            "amount_column",
            "description_column",
        ):
            payload = {**_VALID}
            del payload[missing]
            with pytest.raises(ValidationError):
                CsvProfile(**payload)

    def test_amount_negative_means_invalid_value(self):
        with pytest.raises(ValidationError):
            CsvProfile(**{**_VALID, "amount_negative_means": "neither"})

    def test_delimiter_must_be_single_char(self):
        with pytest.raises(ValidationError):
            CsvProfile(**{**_VALID, "delimiter": ";;"})

    def test_skip_header_rows_non_negative(self):
        with pytest.raises(ValidationError):
            CsvProfile(**{**_VALID, "skip_header_rows": -1})

    def test_name_non_empty(self):
        with pytest.raises(ValidationError):
            CsvProfile(**{**_VALID, "name": ""})

    def test_date_format_non_empty(self):
        with pytest.raises(ValidationError):
            CsvProfile(**{**_VALID, "date_format": ""})


class TestYamlCodec:
    def test_round_trip(self):
        p = CsvProfile(**_VALID)
        recovered = CsvProfile.from_yaml(p.to_yaml())
        assert recovered == p

    def test_to_yaml_is_human_readable(self):
        p = CsvProfile(**_VALID)
        text = p.to_yaml()
        assert "name: chase-checking" in text
        assert "date_format: '%m/%d/%Y'" in text or 'date_format: "%m/%d/%Y"' in text
        # PyYAML's default flow style is block, not inline JSON-y.
        assert "{" not in text

    def test_from_yaml_rejects_unsafe_python_object_tag(self):
        # safe_load rejects !!python/object tags (the historical RCE vector).
        # If this test ever fails we've regressed to yaml.load somewhere.
        unsafe_tag = "!!python/object/apply:builtins.int"
        unsafe = (
            "name: chase\n"
            "date_column: D\n"
            "date_format: '%Y-%m-%d'\n"
            "amount_column: A\n"
            "description_column: X\n"
            f"skip_header_rows: {unsafe_tag} [42]\n"
        )
        import yaml

        # safe_load raises YAMLError on unsafe tags. If this regresses
        # to yaml.load, the load would silently succeed and the model
        # validation below would (incorrectly) pass.
        with pytest.raises((yaml.YAMLError, ValueError, ValidationError)):
            CsvProfile.from_yaml(unsafe)

    def test_from_yaml_rejects_invalid_yaml(self):
        import yaml

        with pytest.raises(yaml.YAMLError):
            CsvProfile.from_yaml("name: : :\n  bad indent")

    def test_from_yaml_rejects_missing_required(self):
        text = "name: chase\n"
        with pytest.raises(ValidationError):
            CsvProfile.from_yaml(text)

    def test_from_yaml_with_extra_fields(self):
        # Extra fields tolerated (forward-compat with future profile fields)
        # but not surfaced on the model.
        text = (
            "name: chase\n"
            "date_column: D\n"
            "date_format: '%Y-%m-%d'\n"
            "amount_column: A\n"
            "description_column: X\n"
            "future_field_we_dont_know_yet: hello\n"
        )
        p = CsvProfile.from_yaml(text)
        assert p.name == "chase"
