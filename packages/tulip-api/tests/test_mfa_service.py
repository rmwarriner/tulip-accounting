"""Tests for the MFA service module (tulip_api.auth.mfa)."""

from __future__ import annotations

import base64
import re

import pyotp
import pytest

from tulip_api.auth.mfa import (
    build_provisioning_uri,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    verify_totp_code,
)

MASTER_KEY = b"\xab" * 32


class TestSecretGeneration:
    def test_generates_base32_secret(self):
        secret = generate_totp_secret()
        # pyotp / Google Authenticator expect base32 secrets.
        assert re.fullmatch(r"[A-Z2-7]+=*", secret)
        # 160 bits = 32 base32 chars (RFC 4226 §4 / RFC 6238).
        assert len(secret) >= 32

    def test_generated_secrets_differ(self):
        a = generate_totp_secret()
        b = generate_totp_secret()
        assert a != b


class TestProvisioningUri:
    def test_builds_otpauth_uri_with_email_and_issuer(self):
        secret = generate_totp_secret()
        uri = build_provisioning_uri(secret=secret, email="alice@example.com", issuer="Tulip")
        assert uri.startswith("otpauth://totp/")
        assert "alice%40example.com" in uri or "alice@example.com" in uri
        assert "issuer=Tulip" in uri
        assert f"secret={secret}" in uri


class TestEncryptDecrypt:
    def _hid(self):
        from uuid import uuid4 as _u

        return _u()

    def _uid(self):
        from uuid import uuid4 as _u

        return _u()

    def test_round_trip(self):
        secret = generate_totp_secret()
        hid, uid = self._hid(), self._uid()
        blob = encrypt_totp_secret(secret, master_key=MASTER_KEY, household_id=hid, user_id=uid)
        # Encrypted form must not equal plaintext.
        assert blob != secret.encode("ascii")
        # Round-trip restores plaintext.
        assert (
            decrypt_totp_secret(blob, master_key=MASTER_KEY, household_id=hid, user_id=uid)
            == secret
        )

    def test_wrong_key_fails(self):
        secret = generate_totp_secret()
        hid, uid = self._hid(), self._uid()
        blob = encrypt_totp_secret(secret, master_key=MASTER_KEY, household_id=hid, user_id=uid)
        with pytest.raises(Exception):  # noqa: B017 — InvalidCiphertextError
            decrypt_totp_secret(blob, master_key=b"\x00" * 32, household_id=hid, user_id=uid)


class TestVerifyTotpCode:
    def test_accepts_current_code(self):
        secret = generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert verify_totp_code(secret, code) is True

    def test_rejects_wrong_code(self):
        secret = generate_totp_secret()
        # 000000 is statistically near-certain not to be the current code.
        # Even on the off chance it is, the next assertion below will fail
        # on a different random secret — flake budget is effectively zero.
        assert verify_totp_code(secret, "000000") is False

    def test_rejects_garbage(self):
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "not-a-code") is False
        assert verify_totp_code(secret, "") is False

    def test_accepts_previous_window(self):
        # ±1 window tolerates clock skew up to ±30s, the standard practice.
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        # Code from 30 seconds ago.
        import time

        past_code = totp.at(int(time.time()) - 30)
        assert verify_totp_code(secret, past_code) is True


def _b32_decodable(secret: str) -> bool:
    try:
        base64.b32decode(secret, casefold=False)
        return True
    except (ValueError, Exception):
        return False
