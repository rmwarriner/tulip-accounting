"""Tests for ``tulip_storage.migrations_meta`` (#135)."""

from __future__ import annotations

import pytest

from tulip_storage.migrations_meta import expected_alembic_head


def test_returns_a_revision_id() -> None:
    """The bundled migrations have a head; revision IDs are 12-char hex."""
    head = expected_alembic_head()
    assert isinstance(head, str)
    assert len(head) == 12
    assert all(c in "0123456789abcdef" for c in head)


def test_matches_a_real_migration_file() -> None:
    """The reported head must correspond to a versions/*.py file.

    Catches the case where the helper drifts from the actual bundled
    migrations (e.g. the package was repackaged without the scripts).
    """
    from pathlib import Path

    head = expected_alembic_head()
    versions_dir = (
        Path(__file__).parent.parent / "src" / "tulip_storage" / "migrations" / "versions"
    )
    matches = list(versions_dir.glob(f"*_{head}_*.py"))
    assert matches, f"head {head!r} not found in {versions_dir}"


def test_consistent_across_calls() -> None:
    """Pure read; no caching gotchas."""
    assert expected_alembic_head() == expected_alembic_head()


def test_raises_when_migrations_dir_empty(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Mis-built package (no version scripts) → RuntimeError, not silent ''."""
    from pathlib import Path

    from tulip_storage import migrations_meta

    empty_migrations = tmp_path / "migrations"
    empty_migrations.mkdir()
    (empty_migrations / "versions").mkdir()
    (empty_migrations / "env.py").write_text("")
    (empty_migrations / "script.py.mako").write_text("")

    monkeypatch.setattr(migrations_meta, "_MIGRATIONS_DIR", Path(empty_migrations))
    with pytest.raises(RuntimeError, match="No alembic head"):
        expected_alembic_head()
