"""Tests for tulip_api.auth.recovery_codes."""

from __future__ import annotations

import re

import pytest

from tulip_api.auth.recovery_codes import (
    DEFAULT_CODE_COUNT,
    generate_recovery_codes,
    hash_recovery_code,
    verify_recovery_code,
)


class TestGenerate:
    def test_default_count_is_eight(self):
        assert len(generate_recovery_codes()) == DEFAULT_CODE_COUNT == 8

    def test_format_is_xxxx_dash_xxxx_base32(self):
        for code in generate_recovery_codes():
            # Two groups of 4 base32 chars, joined by a dash.
            assert re.fullmatch(r"[A-Z2-7]{4}-[A-Z2-7]{4}", code), code

    def test_codes_are_unique_within_a_batch(self):
        codes = generate_recovery_codes()
        assert len(set(codes)) == len(codes)


class TestRoundTrip:
    def test_hash_then_verify(self):
        code = generate_recovery_codes(1)[0]
        h = hash_recovery_code(code)
        assert verify_recovery_code(code, h) is True

    def test_wrong_code_does_not_verify(self):
        h = hash_recovery_code("ABCD-EFGH")
        assert verify_recovery_code("ZZZZ-ZZZZ", h) is False

    @pytest.mark.parametrize(
        "input_form",
        [
            "ABCD-EFGH",  # canonical
            "abcd-efgh",  # lowercase
            "ABCDEFGH",  # no dash
            "abcdefgh",  # lowercase, no dash
            "ABCD EFGH",  # space instead of dash
            "  ABCD-EFGH  ",  # surrounding whitespace
            "ABCD--EFGH",  # extra dash
        ],
    )
    def test_input_normalization(self, input_form: str):
        h = hash_recovery_code("ABCD-EFGH")
        assert verify_recovery_code(input_form, h) is True

    def test_empty_input_does_not_verify(self):
        h = hash_recovery_code("ABCD-EFGH")
        assert verify_recovery_code("", h) is False
        assert verify_recovery_code("---", h) is False  # all dashes → empty after normalize

    def test_garbled_hash_returns_false_not_raises(self):
        # A corrupt row in the DB shouldn't 500 the endpoint.
        assert verify_recovery_code("ABCD-EFGH", "not-a-real-argon2-hash") is False
