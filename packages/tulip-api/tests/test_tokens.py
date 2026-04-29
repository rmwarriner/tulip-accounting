"""Tests for JWT access tokens and opaque refresh tokens."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from tulip_api.auth.tokens import (
    Claims,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_access_token,
)

SECRET = "test-secret-32bytes-test-secret!!"


class TestAccessTokens:
    def test_create_and_verify(self):
        uid = uuid4()
        hid = uuid4()
        token = create_access_token(
            user_id=uid,
            household_id=hid,
            role="admin",
            secret=SECRET,
        )
        claims = verify_access_token(token, secret=SECRET)
        assert isinstance(claims, Claims)
        assert claims.user_id == uid
        assert claims.household_id == hid
        assert claims.role == "admin"

    def test_wrong_secret_rejected(self):
        token = create_access_token(
            user_id=uuid4(), household_id=uuid4(), role="admin", secret=SECRET
        )
        with pytest.raises(InvalidTokenError):
            verify_access_token(token, secret="different" * 4)

    def test_expired_token_rejected(self):
        token = create_access_token(
            user_id=uuid4(),
            household_id=uuid4(),
            role="member",
            secret=SECRET,
            ttl=timedelta(seconds=-1),  # already expired
        )
        with pytest.raises(InvalidTokenError):
            verify_access_token(token, secret=SECRET)

    def test_garbage_token_rejected(self):
        with pytest.raises(InvalidTokenError):
            verify_access_token("not.a.token", secret=SECRET)


class TestRefreshTokens:
    def test_refresh_token_is_opaque_random(self):
        a = create_refresh_token()
        b = create_refresh_token()
        assert a != b
        # Should be url-safe-ish; minimum 32 bytes of entropy.
        assert len(a) >= 32

    def test_hash_is_stable(self):
        rt = create_refresh_token()
        assert hash_refresh_token(rt) == hash_refresh_token(rt)

    def test_hash_is_collision_resistant(self):
        a = create_refresh_token()
        b = create_refresh_token()
        assert hash_refresh_token(a) != hash_refresh_token(b)

    def test_hash_does_not_contain_plaintext(self):
        rt = create_refresh_token()
        h = hash_refresh_token(rt)
        assert rt not in h
