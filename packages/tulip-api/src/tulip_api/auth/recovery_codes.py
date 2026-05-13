"""MFA recovery codes — generation, hashing, verification.

Recovery codes are single-use fall-back logins for users who lose access
to their authenticator app. Generated at TOTP enrollment-verify time;
stored as argon2id hashes (mfa_recovery_codes.code_hash) so the
plaintext is only ever visible once, in the response from
``/v1/auth/mfa/verify`` (or ``/recovery-codes/regenerate``).

Format: four groups of four base32 characters joined by dashes, e.g.
``K3M7-QHJR-9PXC-W2VD``. Eight codes per user. The base32 alphabet
(RFC 4648) is A-Z + 2-7 — no 0/1/8/9 to avoid transcription confusion.
16 chars = 80 bits per code (H-3 in #219). Codes minted before the
length bump remain verifiable: ``_normalize`` only strips formatting,
and ``_HASHER.verify`` works against whatever length the user actually
transcribes.

Input normalization: case-insensitive, dashes optional. Users transcribe
these from a printed backup, so accepting ``k3m7qhjr9pxcw2vd`` and
``K3M7-QHJR-9PXC-W2VD`` identically is the right ergonomic choice.
"""

from __future__ import annotations

import secrets
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

#: RFC 4648 base32 alphabet (A-Z + 2-7), excluding 0/1/8/9 by construction.
_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

#: Number of codes minted per call (per user).
DEFAULT_CODE_COUNT: Final[int] = 8

#: Per-group length and group count. 4*4 = 16 chars = 80 bits per code.
_GROUP_LEN: Final[int] = 4
_GROUP_COUNT: Final[int] = 4
_CODE_CHARS: Final[int] = _GROUP_LEN * _GROUP_COUNT

# Reuse argon2-cffi defaults — same parameters used for passwords. The
# CPU cost during enrollment-verify (8 hashes) is bounded and acceptable.
_HASHER = PasswordHasher()


def generate_recovery_codes(count: int = DEFAULT_CODE_COUNT) -> list[str]:
    """Mint ``count`` fresh, formatted recovery codes (plaintext)."""
    return [_format(_random_chars(_CODE_CHARS)) for _ in range(count)]


def hash_recovery_code(plain: str) -> str:
    """Argon2id-hash a recovery code for storage."""
    return _HASHER.hash(_normalize(plain))


def verify_recovery_code(plain: str, hashed: str) -> bool:
    """Return True iff ``plain`` matches the stored ``hashed`` form.

    Tolerant of case and dashes in the user's input.
    """
    normalized = _normalize(plain)
    if not normalized:
        return False
    try:
        return _HASHER.verify(hashed, normalized)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # A garbled hash means the row is corrupt; treat as a non-match
        # rather than raising, so a single bad row can't 500 the endpoint.
        return False


def _random_chars(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _format(chars: str) -> str:
    groups = [chars[i * _GROUP_LEN : (i + 1) * _GROUP_LEN] for i in range(_GROUP_COUNT)]
    return "-".join(groups)


def _normalize(plain: str) -> str:
    """Strip dashes/whitespace and uppercase. Inverse of ``_format`` for matching."""
    return "".join(c for c in plain.upper() if c in _ALPHABET)
