"""Tests for tulip-storage field-level encryption helpers (v2 + AAD, #338)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tulip_storage.encryption import (
    InvalidCiphertextError,
    InvalidKeyError,
    decrypt_field,
    derive_master_key,
    encrypt_field,
    field_aad,
    wrap_legacy_v1_blob,
)
from tulip_storage.encryption.field import (
    NONCE_SIZE,
    VERSION_BYTE_SIZE,
    VERSION_V1,
    VERSION_V2,
)


@pytest.fixture
def master_key() -> bytes:
    return os.urandom(32)


@pytest.fixture
def aad() -> bytes:
    return field_aad(
        table="users",
        column="totp_secret_encrypted",
        household_id=uuid4(),
        row_id=uuid4(),
    )


class TestEncryptDecryptRoundTrip:
    def test_round_trip_short(self, master_key: bytes, aad: bytes):
        plaintext = b"hello world"
        ct = encrypt_field(plaintext, master_key, aad=aad)
        assert decrypt_field(ct, master_key, aad=aad) == plaintext

    def test_round_trip_long(self, master_key: bytes, aad: bytes):
        plaintext = b"A" * 10_000
        ct = encrypt_field(plaintext, master_key, aad=aad)
        assert decrypt_field(ct, master_key, aad=aad) == plaintext

    def test_round_trip_empty(self, master_key: bytes, aad: bytes):
        plaintext = b""
        ct = encrypt_field(plaintext, master_key, aad=aad)
        assert decrypt_field(ct, master_key, aad=aad) == plaintext

    def test_v2_blob_starts_with_version_byte(self, master_key: bytes, aad: bytes):
        ct = encrypt_field(b"x", master_key, aad=aad)
        assert ct[0] == VERSION_V2


class TestNonceUniqueness:
    def test_two_encryptions_of_same_plaintext_produce_different_ciphertext(
        self, master_key: bytes, aad: bytes
    ):
        a = encrypt_field(b"sensitive", master_key, aad=aad)
        b = encrypt_field(b"sensitive", master_key, aad=aad)
        assert a != b
        assert decrypt_field(a, master_key, aad=aad) == b"sensitive"
        assert decrypt_field(b, master_key, aad=aad) == b"sensitive"


class TestKeyValidation:
    def test_wrong_key_size_raises_on_encrypt(self):
        with pytest.raises(InvalidKeyError):
            encrypt_field(b"x", b"too short", aad=b"")

    def test_wrong_key_size_raises_on_decrypt(self, master_key: bytes, aad: bytes):
        ct = encrypt_field(b"x", master_key, aad=aad)
        with pytest.raises(InvalidKeyError):
            decrypt_field(ct, b"too short", aad=aad)

    def test_wrong_key_raises(self, master_key: bytes, aad: bytes):
        ct = encrypt_field(b"hello", master_key, aad=aad)
        wrong = os.urandom(32)
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(ct, wrong, aad=aad)


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self, master_key: bytes, aad: bytes):
        ct = bytearray(encrypt_field(b"hello", master_key, aad=aad))
        # Flip a bit past the version + nonce header.
        ct[20] ^= 0x01
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(bytes(ct), master_key, aad=aad)

    def test_short_blob_raises(self, master_key: bytes):
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(b"\x02tiny", master_key, aad=b"")

    def test_unknown_version_byte_raises(self, master_key: bytes, aad: bytes):
        # Hand-craft a blob with version=0xFF — must be rejected.
        ct = encrypt_field(b"hello", master_key, aad=aad)
        bad = bytes([0xFF]) + ct[1:]
        with pytest.raises(InvalidCiphertextError, match="unknown ciphertext version"):
            decrypt_field(bad, master_key, aad=aad)


class TestAadBinding:
    """#338, audit M-1: AAD binds the ciphertext to its (column, row) identity."""

    def test_wrong_aad_fails_authentication(self, master_key: bytes):
        right = field_aad(
            table="users",
            column="totp_secret_encrypted",
            household_id=uuid4(),
            row_id=uuid4(),
        )
        wrong = field_aad(
            table="users",
            column="totp_secret_encrypted",
            household_id=uuid4(),  # different household
            row_id=uuid4(),
        )
        ct = encrypt_field(b"secret", master_key, aad=right)
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(ct, master_key, aad=wrong)

    def test_cross_column_swap_rejected(self, master_key: bytes):
        """Swap (users.totp_secret) ciphertext into (accounts.notes) → reject."""
        hid = uuid4()
        user_id = uuid4()
        account_id = uuid4()
        ct = encrypt_field(
            b"my-totp-secret",
            master_key,
            aad=field_aad(
                table="users",
                column="totp_secret_encrypted",
                household_id=hid,
                row_id=user_id,
            ),
        )
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(
                ct,
                master_key,
                aad=field_aad(
                    table="accounts",
                    column="notes_encrypted",
                    household_id=hid,
                    row_id=account_id,
                ),
            )

    def test_cross_household_swap_rejected(self, master_key: bytes):
        """The headline M-1 attack: swap ai_keys between households."""
        col = ("households", "ai_keys_encrypted")
        h_a = uuid4()
        h_b = uuid4()
        # Each household's blob is bound to its own household_id.
        ct_a = encrypt_field(
            b'{"openai":"sk-a..."}',
            master_key,
            aad=field_aad(table=col[0], column=col[1], household_id=h_a, row_id=h_a),
        )
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(
                ct_a,
                master_key,
                aad=field_aad(table=col[0], column=col[1], household_id=h_b, row_id=h_b),
            )

    def test_field_aad_format(self):
        aad = field_aad(table="t", column="c", household_id="H", row_id="R")
        assert aad == b"t:c:H:R"


class TestV1LegacyCompat:
    """Pre-#338 blobs (wrapped as v1) still decrypt without AAD."""

    def test_v1_wrapped_blob_decrypts_with_aad_ignored(self, master_key: bytes):
        # Simulate a pre-#338 blob: encrypted with AAD=None, then wrapped
        # by the migration with the 0x01 prefix.
        import os as _os

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

        nonce = _os.urandom(NONCE_SIZE)
        ct_body = _AESGCM(master_key).encrypt(nonce, b"old-secret", associated_data=None)
        raw_v1 = nonce + ct_body
        wrapped = wrap_legacy_v1_blob(raw_v1)
        assert wrapped[0] == VERSION_V1
        # AAD argument is ignored on v1 — the writer didn't bind it.
        assert decrypt_field(wrapped, master_key, aad=b"any-aad-here") == b"old-secret"
        assert decrypt_field(wrapped, master_key, aad=b"") == b"old-secret"

    def test_wrap_is_idempotent(self, master_key: bytes):
        import os as _os

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

        nonce = _os.urandom(NONCE_SIZE)
        ct_body = _AESGCM(master_key).encrypt(nonce, b"x", associated_data=None)
        raw_v1 = nonce + ct_body
        wrapped_once = wrap_legacy_v1_blob(raw_v1)
        wrapped_twice = wrap_legacy_v1_blob(wrapped_once)
        assert wrapped_once == wrapped_twice

    def test_wrap_too_short_raises(self):
        with pytest.raises(InvalidCiphertextError):
            wrap_legacy_v1_blob(b"tiny")

    def test_v2_passed_through_wrap(self, master_key: bytes, aad: bytes):
        ct_v2 = encrypt_field(b"hi", master_key, aad=aad)
        # wrap_legacy_v1_blob should not double-wrap a v2 blob.
        assert wrap_legacy_v1_blob(ct_v2) == ct_v2


class TestDeriveMasterKey:
    def test_derived_keys_are_32_bytes(self):
        salt = os.urandom(16)
        k1 = derive_master_key("correct-horse-battery-staple", salt)
        assert len(k1) == 32

    def test_derivation_is_deterministic(self):
        salt = os.urandom(16)
        k1 = derive_master_key("password", salt)
        k2 = derive_master_key("password", salt)
        assert k1 == k2

    def test_different_salts_produce_different_keys(self):
        k1 = derive_master_key("password", os.urandom(16))
        k2 = derive_master_key("password", os.urandom(16))
        assert k1 != k2

    def test_different_passphrases_produce_different_keys(self):
        salt = os.urandom(16)
        k1 = derive_master_key("password1", salt)
        k2 = derive_master_key("password2", salt)
        assert k1 != k2


class TestWireFormat:
    def test_v2_length_grows_by_one_byte_versus_legacy(self, master_key: bytes, aad: bytes):
        """v2 = 1 (version) + 12 (nonce) + len(pt) + 16 (tag)."""
        pt = b"X" * 32
        ct = encrypt_field(pt, master_key, aad=aad)
        assert len(ct) == VERSION_BYTE_SIZE + NONCE_SIZE + len(pt) + 16
