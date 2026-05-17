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


class TestArgon2ParamsArePinned:
    """#328 / security audit M-24: argon2id parameters are pinned in
    ``argon2_params.py`` and applied to both the password hasher and
    the recovery-code hasher. A future ``argon2-cffi`` default-tune
    cannot silently shift parameters across the fleet.
    """

    def test_password_hasher_uses_pinned_params(self):
        from tulip_api.auth.argon2_params import (
            HASH_LEN,
            MEMORY_COST,
            PARALLELISM,
            SALT_LEN,
            TIME_COST,
        )
        from tulip_api.auth.passwords import _HASHER

        assert _HASHER.time_cost == TIME_COST
        assert _HASHER.memory_cost == MEMORY_COST
        assert _HASHER.parallelism == PARALLELISM
        assert _HASHER.hash_len == HASH_LEN
        assert _HASHER.salt_len == SALT_LEN

    def test_recovery_code_hasher_uses_pinned_params(self):
        from tulip_api.auth.argon2_params import (
            HASH_LEN,
            MEMORY_COST,
            PARALLELISM,
            SALT_LEN,
            TIME_COST,
        )
        from tulip_api.auth.recovery_codes import _HASHER

        assert _HASHER.time_cost == TIME_COST
        assert _HASHER.memory_cost == MEMORY_COST
        assert _HASHER.parallelism == PARALLELISM
        assert _HASHER.hash_len == HASH_LEN
        assert _HASHER.salt_len == SALT_LEN

    def test_pinned_params_exceed_owasp_2024_minimums(self):
        """OWASP 2024 minimum: m=19 MiB, t=2, p=1."""
        from tulip_api.auth.argon2_params import (
            MEMORY_COST,
            PARALLELISM,
            TIME_COST,
        )

        assert MEMORY_COST >= 19 * 1024
        assert TIME_COST >= 2
        assert PARALLELISM >= 1
