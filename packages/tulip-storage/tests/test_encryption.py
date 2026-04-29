"""Tests for tulip-storage field-level encryption helpers."""

from __future__ import annotations

import os

import pytest

from tulip_storage.encryption import (
    InvalidCiphertextError,
    InvalidKeyError,
    decrypt_field,
    derive_master_key,
    encrypt_field,
)


@pytest.fixture
def master_key() -> bytes:
    return os.urandom(32)


class TestEncryptDecryptRoundTrip:
    def test_round_trip_short(self, master_key: bytes):
        plaintext = b"hello world"
        ct = encrypt_field(plaintext, master_key)
        assert decrypt_field(ct, master_key) == plaintext

    def test_round_trip_long(self, master_key: bytes):
        plaintext = b"A" * 10_000
        ct = encrypt_field(plaintext, master_key)
        assert decrypt_field(ct, master_key) == plaintext

    def test_round_trip_empty(self, master_key: bytes):
        plaintext = b""
        ct = encrypt_field(plaintext, master_key)
        assert decrypt_field(ct, master_key) == plaintext


class TestNonceUniqueness:
    def test_two_encryptions_of_same_plaintext_produce_different_ciphertext(
        self, master_key: bytes
    ):
        # AES-GCM with random nonce → identical plaintexts must encrypt to
        # distinct ciphertexts (otherwise an attacker can detect repeats).
        a = encrypt_field(b"sensitive", master_key)
        b = encrypt_field(b"sensitive", master_key)
        assert a != b
        assert decrypt_field(a, master_key) == b"sensitive"
        assert decrypt_field(b, master_key) == b"sensitive"


class TestKeyValidation:
    def test_wrong_key_size_raises_on_encrypt(self):
        with pytest.raises(InvalidKeyError):
            encrypt_field(b"x", b"too short")

    def test_wrong_key_size_raises_on_decrypt(self, master_key: bytes):
        ct = encrypt_field(b"x", master_key)
        with pytest.raises(InvalidKeyError):
            decrypt_field(ct, b"too short")

    def test_wrong_key_raises(self, master_key: bytes):
        ct = encrypt_field(b"hello", master_key)
        wrong = os.urandom(32)
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(ct, wrong)


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self, master_key: bytes):
        ct = bytearray(encrypt_field(b"hello", master_key))
        # Flip a bit in the ciphertext body (avoid the 12-byte nonce header).
        ct[20] ^= 0x01
        with pytest.raises(InvalidCiphertextError):
            decrypt_field(bytes(ct), master_key)


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
