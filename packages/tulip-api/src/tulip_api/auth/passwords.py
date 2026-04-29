"""Argon2id password hashing.

Wraps `argon2-cffi`. Hashes are PHC-formatted strings so they self-describe
their parameters; that lets `needs_rehash` tell us when to re-hash with
upgraded parameters at the user's next successful login.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# OWASP 2024 minimum: m=19MiB, t=2, p=1. argon2-cffi defaults are stricter
# than that already (m=64MiB, t=3, p=4) — keep them. If we re-tune later,
# `needs_rehash` will flag old hashes for upgrade-on-next-login.
_HASHER = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return a PHC-formatted argon2id hash of `plain`."""
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches the given argon2id hash.

    Raises ValueError when `hashed` is not a valid argon2 PHC string.
    """
    try:
        return _HASHER.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except InvalidHashError as exc:
        raise ValueError("hashed value is not a valid argon2 PHC string") from exc


def needs_rehash(hashed: str) -> bool:
    """Return True if `hashed` was generated with stale parameters."""
    return _HASHER.check_needs_rehash(hashed)
