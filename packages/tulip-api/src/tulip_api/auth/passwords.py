"""Argon2id password hashing.

Wraps `argon2-cffi`. Hashes are PHC-formatted strings so they self-describe
their parameters; that lets `needs_rehash` tell us when to re-hash with
upgraded parameters at the user's next successful login.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from tulip_api.auth.argon2_params import (
    HASH_LEN,
    MEMORY_COST,
    PARALLELISM,
    SALT_LEN,
    TIME_COST,
)

# Parameters pinned explicitly per security audit M-24 (#328). Current
# values match the argon2-cffi defaults at audit time (m=64MiB, t=3, p=4)
# and exceed OWASP 2024 minimums (m=19MiB, t=2, p=1). Tune by editing
# ``argon2_params.py``; ``needs_rehash`` will flag stale hashes for
# upgrade-on-next-login per #224.
_HASHER = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST,
    parallelism=PARALLELISM,
    hash_len=HASH_LEN,
    salt_len=SALT_LEN,
)


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
