"""Pinned argon2id parameters for password + recovery-code hashing.

Per the deep security audit's M-24: ``argon2-cffi`` library defaults
exceed OWASP 2024 minimums, but they are not pinned, not surfaced via
diagnostics, and not configurable. A future ``argon2-cffi`` default
change would silently shift parameters across the fleet. Pin them
explicitly here; tune by editing this file (and ``needs_rehash`` will
flag stale hashes for upgrade-on-next-login per #224).

Current values match the library defaults at the time of audit (2026-
05) and exceed OWASP 2024 minimums (m=19 MiB, t=2, p=1) comfortably.
"""

from __future__ import annotations

from typing import Final

#: Iterations.
TIME_COST: Final[int] = 3

#: Memory cost in KiB (65 536 KiB = 64 MiB).
MEMORY_COST: Final[int] = 65_536

#: Parallelism (lanes).
PARALLELISM: Final[int] = 4

#: Output length, bytes.
HASH_LEN: Final[int] = 32

#: Salt length, bytes.
SALT_LEN: Final[int] = 16
