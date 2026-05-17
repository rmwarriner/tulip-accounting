"""TOTP (RFC 6238) helpers for the MFA enrollment + verification flows.

Wraps :mod:`pyotp` for secret generation, provisioning-URI construction,
and code verification, plus AES-256-GCM encrypt/decrypt of stored
secrets via the field-encryption helper in ``tulip-storage``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

import pyotp

from tulip_storage.encryption.field import decrypt_field, encrypt_field, field_aad

if TYPE_CHECKING:
    from uuid import UUID

#: Default issuer name shown in authenticator apps.
DEFAULT_ISSUER: Final[str] = "Tulip Accounting"

#: TOTP step size in seconds per RFC 6238. Hard-coded; changing this
#: would invalidate every enrolled secret.
_STEP_SECONDS: Final[int] = 30

#: ±1 30-second windows of slack tolerated when verifying codes — i.e.
#: the previous and next windows count as valid. Standard practice for
#: TOTP to absorb clock skew.
_VERIFY_WINDOW: Final[int] = 1


def generate_totp_secret() -> str:
    """Return a fresh base32-encoded TOTP secret (160 bits)."""
    return pyotp.random_base32()


def build_provisioning_uri(*, secret: str, email: str, issuer: str = DEFAULT_ISSUER) -> str:
    """Return an ``otpauth://`` URI suitable for QR-encoding."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp_code(
    secret: str, code: str, *, last_step: int | None = None
) -> tuple[bool, int | None]:
    """Return ``(verified, step)`` for a candidate TOTP.

    ``step`` is the Unix-epoch-seconds / 30 of the matched window, so
    callers can persist it in ``users.last_totp_step`` to defeat replays
    (security audit M-5, #330). When ``last_step`` is provided, any
    candidate step ``<= last_step`` is treated as a replay and rejected
    even if the code matches.

    The valid window is ``[current - VERIFY_WINDOW, current + VERIFY_WINDOW]``;
    the loop checks each step explicitly so we can both honour the
    replay gate AND learn which step matched (``pyotp.TOTP.verify``'s
    bool answer doesn't tell us).

    Returns ``(False, None)`` on malformed input, replay, or no match.
    """
    if not code or not code.isdigit():
        return (False, None)
    totp = pyotp.TOTP(secret)
    current_step = int(time.time()) // _STEP_SECONDS
    for offset in range(-_VERIFY_WINDOW, _VERIFY_WINDOW + 1):
        candidate_step = current_step + offset
        if last_step is not None and candidate_step <= last_step:
            continue
        # ``totp.at`` takes a Unix timestamp (seconds), not a step number,
        # so multiply back to seconds for the lookup.
        if totp.at(candidate_step * _STEP_SECONDS) == code:
            return (True, candidate_step)
    return (False, None)


def _totp_aad(*, household_id: UUID, user_id: UUID) -> bytes:
    """AAD binding for the (users.totp_secret_encrypted, user-row) field (#338)."""
    return field_aad(
        table="users",
        column="totp_secret_encrypted",
        household_id=household_id,
        row_id=user_id,
    )


def encrypt_totp_secret(
    secret: str,
    *,
    master_key: bytes,
    household_id: UUID,
    user_id: UUID,
) -> bytes:
    """Encrypt a base32 TOTP secret for storage in ``users.totp_secret_encrypted``.

    AAD binds the ciphertext to ``(household_id, user_id)`` so a future
    DB-write attacker can't swap user A's TOTP secret onto user B (audit M-1).
    """
    return encrypt_field(
        secret.encode("ascii"),
        master_key=master_key,
        aad=_totp_aad(household_id=household_id, user_id=user_id),
    )


def decrypt_totp_secret(
    blob: bytes,
    *,
    master_key: bytes,
    household_id: UUID,
    user_id: UUID,
) -> str:
    """Decrypt a blob produced by :func:`encrypt_totp_secret`.

    Reconstructs the same AAD the writer used; legacy v1 blobs decrypt
    transparently without consulting the AAD.
    """
    return decrypt_field(
        blob,
        master_key=master_key,
        aad=_totp_aad(household_id=household_id, user_id=user_id),
    ).decode("ascii")
