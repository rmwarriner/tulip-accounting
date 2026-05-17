"""AES-256-GCM field-level encryption.

Storage format for an encrypted blob:

    | 12 bytes nonce | ciphertext + 16-byte GCM tag |

The 32-byte master key encrypts directly. A future revision will introduce
per-field DEKs wrapped by the master key (key-rotation story); the
encrypt/decrypt API surface is designed so that change is internal.

Key derivation is PBKDF2-HMAC-SHA256 with a high iteration count. The
expected workflow: operator enters a passphrase at server startup; we
derive the master key once, hold it in process memory only, and never
write it to disk in plaintext.
"""

from __future__ import annotations

import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEY_SIZE: Final[int] = 32  # AES-256
NONCE_SIZE: Final[int] = 12  # 96-bit nonce — AES-GCM standard
PBKDF2_ITERATIONS: Final[int] = 600_000  # OWASP 2024 guidance for PBKDF2-SHA256


class InvalidKeyError(ValueError):
    """Raised when a key is the wrong length or otherwise unusable."""


class InvalidCiphertextError(ValueError):
    """Raised when ciphertext fails authentication (tampered or wrong key)."""


def _validate_key(key: bytes) -> None:
    if len(key) != KEY_SIZE:
        raise InvalidKeyError(f"master key must be exactly {KEY_SIZE} bytes (got {len(key)})")


def encrypt_field(plaintext: bytes, master_key: bytes) -> bytes:
    """Encrypt `plaintext` with AES-256-GCM and a fresh random nonce.

    Returns: nonce (12 bytes) + ciphertext + tag (16 bytes).
    """
    _validate_key(master_key)
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(master_key)
    ct = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    return nonce + ct


def decrypt_field(blob: bytes, master_key: bytes) -> bytes:
    """Decrypt a blob produced by encrypt_field.

    Raises:
        InvalidKeyError: master_key is the wrong size.
        InvalidCiphertextError: blob is too short, has been tampered with,
            or was encrypted with a different key.

    """
    _validate_key(master_key)
    if len(blob) < NONCE_SIZE + 16:
        raise InvalidCiphertextError("blob too short to contain nonce + tag")
    nonce = blob[:NONCE_SIZE]
    ct = blob[NONCE_SIZE:]
    aesgcm = AESGCM(master_key)
    try:
        return aesgcm.decrypt(nonce, ct, associated_data=None)
    except InvalidTag as exc:
        raise InvalidCiphertextError("ciphertext authentication failed") from exc


def derive_master_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES-256 master key from a passphrase via PBKDF2-HMAC-SHA256.

    .. warning::
       **Not wired into any production code path today.** ``TULIP_MASTER_KEY``
       / ``TULIP_KEY_FILE`` carry a pre-derived 32-byte key directly; the
       passphrase-derivation path is reserved for a future Phase-10 KMS /
       passphrase-unlock flow. The helper has hypothesis-style unit tests
       so it stays correct against the PBKDF2 contract — security audit L-8
       (#352) called this out so a future wiring doesn't ship a regressed
       derivation.

    The salt should be at least 16 bytes and persisted across restarts (it
    is not secret; it just defeats rainbow tables). Iterations follow OWASP
    2024 guidance.
    """
    if len(salt) < 16:
        raise InvalidKeyError(f"salt must be at least 16 bytes (got {len(salt)})")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))
