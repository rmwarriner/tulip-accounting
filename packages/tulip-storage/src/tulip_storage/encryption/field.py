"""AES-256-GCM field-level encryption with versioned wire format + AAD.

Storage format (#338, audit M-1 + M-6):

    | 1 byte version | 12 bytes nonce | ciphertext + 16-byte GCM tag |

Two versions in circulation today:

- ``v1 = 0x01`` — legacy. AEAD ``associated_data`` is ``None``. Written
  by the pre-#338 encrypt_field. The #338 backfill migration wraps every
  raw-v1 blob (which had no version prefix) with the ``0x01`` byte so
  that all blobs at rest have explicit versioning.

- ``v2 = 0x02`` — current. AEAD ``associated_data`` is the caller-
  supplied ``aad`` parameter, typically built via :func:`field_aad` from
  ``(table, column, household_id, row_id)``. The AAD is *not* stored in
  the blob — the decrypt caller must reconstruct it from the row's
  identity. This binds the ciphertext to its (column, row) and defeats
  the cross-row / cross-column swap that audit M-1 documented for v1.

The 32-byte master key encrypts directly. A future revision (#XX) will
introduce per-field DEKs wrapped by the master key; the version byte
gives that change a non-invasive on-disk dispatch.

Key derivation is PBKDF2-HMAC-SHA256 with a high iteration count
(operator-passphrase derivation; ``derive_master_key`` below) — not
wired into any production path today but kept correct against the OWASP
contract for the eventual Phase-10 KMS / passphrase-unlock flow.
"""

from __future__ import annotations

import os
from typing import Final
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEY_SIZE: Final[int] = 32  # AES-256
NONCE_SIZE: Final[int] = 12  # 96-bit nonce — AES-GCM standard
PBKDF2_ITERATIONS: Final[int] = 600_000  # OWASP 2024 guidance for PBKDF2-SHA256
GCM_TAG_SIZE: Final[int] = 16

#: Versioned wire format prefix. See module docstring for the version
#: history. New writes go via ``v2``; ``v1`` is supported on read only.
VERSION_V1: Final[int] = 0x01
VERSION_V2: Final[int] = 0x02
VERSION_BYTE_SIZE: Final[int] = 1


class InvalidKeyError(ValueError):
    """Raised when a key is the wrong length or otherwise unusable."""


class InvalidCiphertextError(ValueError):
    """Raised when ciphertext fails authentication (tampered or wrong key)."""


def _validate_key(key: bytes) -> None:
    if len(key) != KEY_SIZE:
        raise InvalidKeyError(f"master key must be exactly {KEY_SIZE} bytes (got {len(key)})")


def field_aad(
    *,
    table: str,
    column: str,
    household_id: UUID | str,
    row_id: UUID | str,
) -> bytes:
    """Build the AAD for a field-encrypted column.

    Format: ``f"{table}:{column}:{household_id}:{row_id}".encode("utf-8")``.

    Binding all four components into the AEAD authentication tag means
    that an attacker with DB write access cannot swap a ciphertext from
    (user A, column totp) into (user B, column totp) — the AEAD will
    refuse to decrypt because the AAD differs. Audit M-1.
    """
    return f"{table}:{column}:{household_id}:{row_id}".encode()


def encrypt_field(
    plaintext: bytes,
    master_key: bytes,
    *,
    aad: bytes,
) -> bytes:
    """Encrypt ``plaintext`` with AES-256-GCM under the v2 wire format.

    Returns: ``0x02 || nonce(12) || ciphertext + tag``.

    ``aad`` is the row-identity AAD (typically from :func:`field_aad`).
    Pass ``b""`` only when the caller genuinely has no row identity to
    bind to (e.g. an admin-tool blob); the more specific the AAD, the
    stronger the cross-row swap guarantee.
    """
    _validate_key(master_key)
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(master_key)
    ct = aesgcm.encrypt(nonce, plaintext, associated_data=aad)
    return bytes([VERSION_V2]) + nonce + ct


def decrypt_field(
    blob: bytes,
    master_key: bytes,
    *,
    aad: bytes,
) -> bytes:
    """Decrypt a blob produced by :func:`encrypt_field` (v2) or its v1 ancestor.

    Dispatches on the leading version byte:

    - ``0x02`` (v2): authenticates the supplied ``aad`` against the
      AEAD tag. Caller must reconstruct the same AAD that the writer
      used (typically :func:`field_aad` with the row's identity).
    - ``0x01`` (v1, legacy): ignores ``aad`` and decrypts with
      ``associated_data=None``. Provided for the migration window where
      not every blob has been rewritten to v2.

    Raises:
        InvalidKeyError: master_key is the wrong size.
        InvalidCiphertextError: blob too short, version byte unknown,
            authentication failed (tampered, wrong key, or for v2 the
            AAD doesn't match what the writer used).

    """
    _validate_key(master_key)
    if len(blob) < VERSION_BYTE_SIZE + NONCE_SIZE + GCM_TAG_SIZE:
        raise InvalidCiphertextError("blob too short to contain version + nonce + tag")
    version = blob[0]
    body = blob[VERSION_BYTE_SIZE:]
    nonce = body[:NONCE_SIZE]
    ct = body[NONCE_SIZE:]
    aesgcm = AESGCM(master_key)
    try:
        if version == VERSION_V2:
            return aesgcm.decrypt(nonce, ct, associated_data=aad)
        if version == VERSION_V1:
            # Legacy: no AAD. ``aad`` argument is ignored here because
            # the v1 writer didn't bind any AAD into the tag.
            return aesgcm.decrypt(nonce, ct, associated_data=None)
        raise InvalidCiphertextError(f"unknown ciphertext version byte: 0x{version:02x}")
    except InvalidTag as exc:
        raise InvalidCiphertextError("ciphertext authentication failed") from exc


def wrap_legacy_v1_blob(raw_v1_blob: bytes) -> bytes:
    """Prefix a pre-#338 raw blob (nonce || ct + tag) with the ``0x01`` version byte.

    Used by the #338 backfill migration to upgrade every existing blob
    on disk to the version-prefixed wire format so :func:`decrypt_field`
    can dispatch unambiguously. The plaintext is unchanged — only the
    on-disk envelope grows by one byte. No master key is required.

    Idempotent: if ``raw_v1_blob`` already starts with a known version
    byte AND its length is consistent with that version, it is returned
    unchanged. This makes the migration safe to re-run if it crashes
    halfway.
    """
    if len(raw_v1_blob) < NONCE_SIZE + GCM_TAG_SIZE:
        raise InvalidCiphertextError("blob too short to be a pre-#338 v1 ciphertext")
    if len(raw_v1_blob) >= VERSION_BYTE_SIZE + NONCE_SIZE + GCM_TAG_SIZE and raw_v1_blob[0] in (
        VERSION_V1,
        VERSION_V2,
    ):
        # Already wrapped — idempotent re-run.
        return raw_v1_blob
    return bytes([VERSION_V1]) + raw_v1_blob


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
