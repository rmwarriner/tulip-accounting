"""Tests for argon2id password hashing helpers."""

from __future__ import annotations

import pytest

from tulip_api.auth.passwords import hash_password, needs_rehash, verify_password


class TestHashAndVerify:
    def test_hash_then_verify(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h) is True

    def test_verify_rejects_wrong_password(self):
        h = hash_password("a")
        assert verify_password("b", h) is False

    def test_two_hashes_of_same_password_differ(self):
        # Argon2 includes a random salt → identical inputs hash differently.
        a = hash_password("p")
        b = hash_password("p")
        assert a != b

    def test_hash_is_argon2id_format(self):
        h = hash_password("p")
        assert h.startswith("$argon2id$")


class TestNeedsRehash:
    def test_fresh_hash_does_not_need_rehash(self):
        h = hash_password("p")
        assert needs_rehash(h) is False

    def test_unknown_hash_format_raises(self):
        with pytest.raises(ValueError):
            verify_password("x", "not-a-hash")
