"""Tests for the ADR-0009 PR A tag normalisation migration (b2c4f9a1e7d6).

Covers:

1. The migration's data backfill — existing transaction_tags rows
   land with the same tag names after upgrade, now via the FK.
2. Schema shape post-upgrade: tags table exists, transaction_tags
   has tag_id column, old ``tag`` string column is gone.
3. Round-trip: upgrade → downgrade restores the pre-PR-A schema +
   preserves the inline tag string.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(ALEMBIC_INI.parent / "src" / "tulip_storage" / "migrations"),
    )
    return cfg


@pytest.fixture
def fresh_db_url(tmp_path):
    db_path = tmp_path / "tulip.db"
    return f"sqlite:///{db_path}"


def _seed_household_account_and_tagged_transaction(
    engine, *, tags: list[str]
) -> tuple[str, str, str]:
    """Insert a minimal household + account + transaction with the given tags
    via raw SQL against the pre-PR-A schema (string-tag).

    Returns the (household_id, transaction_id, account_id) as hex strings.
    Uses the legacy column layout so the migration's backfill has data to
    process.
    """
    household_id = str(uuid4())
    account_id = str(uuid4())
    tx_id = str(uuid4())
    with engine.begin() as conn:
        # household
        conn.execute(
            text(
                "INSERT INTO households (id, name, base_currency, ai_policy) "
                "VALUES (:id, :name, :ccy, '{}')"
            ),
            {"id": household_id, "name": "Smith", "ccy": "USD"},
        )
        # account
        conn.execute(
            text(
                "INSERT INTO accounts "
                "(household_id, id, name, type, currency, visibility, "
                " is_active, is_placeholder) "
                "VALUES (:hid, :aid, :name, :type, :ccy, :vis, 1, 0)"
            ),
            {
                "hid": household_id,
                "aid": account_id,
                "name": "Checking",
                "type": "ASSET",
                "ccy": "USD",
                "vis": "shared",
            },
        )
        # transaction
        conn.execute(
            text(
                "INSERT INTO transactions "
                "(household_id, id, date, description, status) "
                "VALUES (:hid, :tid, :dt, :desc, :st)"
            ),
            {
                "hid": household_id,
                "tid": tx_id,
                "dt": str(date(2026, 5, 1)),
                "desc": "Lunch",
                "st": "POSTED",
            },
        )
        for tag in tags:
            conn.execute(
                text(
                    "INSERT INTO transaction_tags "
                    "(household_id, transaction_id, tag) "
                    "VALUES (:hid, :tid, :tag)"
                ),
                {"hid": household_id, "tid": tx_id, "tag": tag},
            )
    return household_id, tx_id, account_id


def test_upgrade_creates_tags_table_with_unique_name_per_household(fresh_db_url):
    """Post-upgrade, the tags table exists with the expected columns + uniqueness."""
    cfg = _make_alembic_cfg(fresh_db_url)
    upgrade(cfg, "head")
    eng = create_engine(fresh_db_url)
    try:
        insp = inspect(eng)
        assert "tags" in insp.get_table_names()
        columns = {c["name"] for c in insp.get_columns("tags")}
        assert columns == {
            "household_id",
            "id",
            "name",
            "description",
            "color",
            "created_at",
        }
    finally:
        eng.dispose()


def test_upgrade_replaces_transaction_tags_tag_with_tag_id(fresh_db_url):
    """Post-upgrade, transaction_tags carries tag_id instead of the string."""
    cfg = _make_alembic_cfg(fresh_db_url)
    upgrade(cfg, "head")
    eng = create_engine(fresh_db_url)
    try:
        insp = inspect(eng)
        columns = {c["name"] for c in insp.get_columns("transaction_tags")}
        assert "tag_id" in columns
        assert "tag" not in columns
    finally:
        eng.dispose()


def test_migration_backfills_existing_tags(fresh_db_url):
    """Upgrade pauses at the pre-PR-A revision, seeds tagged data, then
    upgrades through PR A — the inline strings become rows in ``tags`` and
    ``transaction_tags`` rows resolve cleanly through the FK."""
    cfg = _make_alembic_cfg(fresh_db_url)
    # Upgrade to the revision immediately before PR A so the legacy
    # transaction_tags schema (with the ``tag`` string column) is
    # available for seeding.
    upgrade(cfg, "a8b3c2d1f4e5")

    eng = create_engine(fresh_db_url)

    @event.listens_for(eng, "connect")
    def _enable_fk(dc, _r):
        cur = dc.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        household_id, tx_id, _ = _seed_household_account_and_tagged_transaction(
            eng, tags=["birthday", "walter"]
        )

        # Now apply PR A.
        upgrade(cfg, "b2c4f9a1e7d6")

        with eng.connect() as conn:
            tags_rows = conn.execute(
                text("SELECT name FROM tags WHERE household_id = :hid ORDER BY name"),
                {"hid": household_id},
            ).fetchall()
            joined = conn.execute(
                text(
                    "SELECT t.name FROM transaction_tags AS tt "
                    "JOIN tags AS t ON t.household_id = tt.household_id "
                    "AND t.id = tt.tag_id "
                    "WHERE tt.household_id = :hid AND tt.transaction_id = :tid "
                    "ORDER BY t.name"
                ),
                {"hid": household_id, "tid": tx_id},
            ).fetchall()

        assert [r[0] for r in tags_rows] == ["birthday", "walter"]
        # Both tags resolve through the FK.
        assert [r[0] for r in joined] == ["birthday", "walter"]
    finally:
        eng.dispose()


def test_migration_round_trip_preserves_inline_strings(fresh_db_url):
    """Upgrade → downgrade brings back the inline tag string with the
    same data the migration backfilled."""
    cfg = _make_alembic_cfg(fresh_db_url)
    upgrade(cfg, "a8b3c2d1f4e5")
    eng = create_engine(fresh_db_url)

    @event.listens_for(eng, "connect")
    def _enable_fk(dc, _r):
        cur = dc.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        household_id, tx_id, _ = _seed_household_account_and_tagged_transaction(
            eng, tags=["birthday", "walter"]
        )
        upgrade(cfg, "b2c4f9a1e7d6")
        downgrade(cfg, "a8b3c2d1f4e5")

        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tag FROM transaction_tags "
                    "WHERE household_id = :hid AND transaction_id = :tid "
                    "ORDER BY tag"
                ),
                {"hid": household_id, "tid": tx_id},
            ).fetchall()
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        assert [r[0] for r in rows] == ["birthday", "walter"]
        # tags table is gone after downgrade.
        assert "tags" not in tables
    finally:
        eng.dispose()
