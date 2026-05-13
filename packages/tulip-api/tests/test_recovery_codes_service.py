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

    def test_format_is_four_groups_of_four_base32(self):
        for code in generate_recovery_codes():
            # H-3 (#219): four groups of 4 base32 chars = 16 chars = 80 bits.
            assert re.fullmatch(r"[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}", code), code

    def test_entropy_is_at_least_80_bits(self):
        # H-3 (#219): bumped from 40 bits to 80 bits to make offline brute
        # force against a stolen argon2id hash infeasible.
        for code in generate_recovery_codes():
            assert len(code.replace("-", "")) == 16

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
            "ABCD-EFGH-IJKL-MNOP",  # canonical
            "abcd-efgh-ijkl-mnop",  # lowercase
            "ABCDEFGHIJKLMNOP",  # no dashes
            "abcdefghijklmnop",  # lowercase, no dashes
            "ABCD EFGH IJKL MNOP",  # spaces instead of dashes
            "  ABCD-EFGH-IJKL-MNOP  ",  # surrounding whitespace
            "ABCD--EFGH--IJKL--MNOP",  # extra dashes
        ],
    )
    def test_input_normalization(self, input_form: str):
        h = hash_recovery_code("ABCD-EFGH-IJKL-MNOP")
        assert verify_recovery_code(input_form, h) is True

    def test_empty_input_does_not_verify(self):
        h = hash_recovery_code("ABCD-EFGH-IJKL-MNOP")
        assert verify_recovery_code("", h) is False
        assert verify_recovery_code("---", h) is False  # all dashes → empty after normalize

    def test_garbled_hash_returns_false_not_raises(self):
        # A corrupt row in the DB shouldn't 500 the endpoint.
        assert verify_recovery_code("ABCD-EFGH-IJKL-MNOP", "not-a-real-argon2-hash") is False

    def test_legacy_short_codes_still_verify(self):
        # Pre-H-3 (#219) codes were 8 chars. The on-disk argon2id hashes
        # are length-agnostic, so existing users who haven't regenerated
        # can still log in with their original short codes.
        h = hash_recovery_code("ABCD-EFGH")
        assert verify_recovery_code("ABCD-EFGH", h) is True
        assert verify_recovery_code("abcdefgh", h) is True
