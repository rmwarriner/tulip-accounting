"""Field-level encryption for sensitive columns.

Used for: account numbers, free-text notes, TOTP secrets, AI provider keys.
Higher layers (SQLCipher full-DB encryption, attachment file encryption)
are deferred to later phases; the field-level helpers here are pure
Python and independent of the underlying DB encryption.

Master keys are 32 bytes (AES-256). Use `derive_master_key` to derive a
key from an operator-supplied passphrase + salt.
"""

from tulip_storage.encryption.field import (
    InvalidCiphertextError,
    InvalidKeyError,
    decrypt_field,
    derive_master_key,
    encrypt_field,
    field_aad,
    wrap_legacy_v1_blob,
)

__all__ = [
    "InvalidCiphertextError",
    "InvalidKeyError",
    "decrypt_field",
    "derive_master_key",
    "encrypt_field",
    "field_aad",
    "wrap_legacy_v1_blob",
]
