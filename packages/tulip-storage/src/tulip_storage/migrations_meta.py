"""Read-only metadata about the alembic migrations bundled with this package.

The doctor CLI (#135) compares the head of the bundled migrations against
the head currently stamped in a deployed database; mismatches surface as
"DB is behind the wheel — run ``alembic upgrade head``". This module
exposes that lookup without booting the alembic CLI.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


def expected_alembic_head() -> str:
    """Return the head revision of the migrations bundled in this package.

    Raises:
        RuntimeError: if the migrations directory has no head revision —
            should only happen if the package is mis-built.

    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        msg = (
            f"No alembic head found in bundled migrations at {_MIGRATIONS_DIR}. "
            "This is a packaging bug — the wheel is missing migration scripts."
        )
        raise RuntimeError(msg)
    return head


__all__ = ["expected_alembic_head"]
