"""Tests for ``tulip_ai.sql_safety`` (P6.2 — NL query SQL gate)."""

from __future__ import annotations

import pytest

from tulip_ai.sql_safety import (
    UnsafeSQLError,
    schema_card,
    validate_and_rewrite,
)

HID = "00000000-0000-0000-0000-000000000001"


class TestRejectsDangerousStatements:
    @pytest.mark.parametrize(
        "sql",
        [
            "",
            "   ",
            "UPDATE transactions SET status='POSTED'",
            "DELETE FROM transactions",
            "INSERT INTO transactions (id) VALUES ('x')",
            "DROP TABLE transactions",
            "CREATE TABLE foo (id INTEGER)",
            "ALTER TABLE transactions ADD COLUMN x TEXT",
            "PRAGMA foreign_keys = OFF",
            "VACUUM",
            "ATTACH 'other.db' AS other",
            "SELECT 1; DROP TABLE transactions",  # multi-statement
            "SELECT 1",  # references no tables
            "SELECT * FROM users",  # not an AI view
            "SELECT * FROM transactions",  # raw table, not the view
        ],
    )
    def test_dangerous_or_off_allowlist_rejected(self, sql: str) -> None:
        with pytest.raises(UnsafeSQLError):
            validate_and_rewrite(sql, household_id=HID)

    def test_parse_error_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="could not parse"):
            validate_and_rewrite("SELECT WHERE FROM BY GROUP", household_id=HID)


class TestAcceptsAndRewrites:
    def test_simple_select_gets_tenant_scoped(self) -> None:
        safe = validate_and_rewrite(
            "SELECT account_code, SUM(amount) FROM ai_view_transactions GROUP BY account_code",
            household_id=HID,
        )
        # Subquery substitution leaves the alias visible; the SQL hits
        # transactions / postings / accounts with the household_id filter.
        assert "ai_view_transactions" in safe.sql  # alias preserved
        assert "WHERE t.household_id = :household_id" in safe.sql
        assert safe.parameters == {"household_id": HID}

    def test_existing_limit_kept(self) -> None:
        safe = validate_and_rewrite(
            "SELECT * FROM ai_view_transactions LIMIT 7",
            household_id=HID,
        )
        assert "LIMIT 7" in safe.sql

    def test_missing_limit_auto_bounded(self) -> None:
        safe = validate_and_rewrite(
            "SELECT * FROM ai_view_transactions",
            household_id=HID,
        )
        assert "LIMIT 100" in safe.sql

    def test_where_clause_preserved(self) -> None:
        safe = validate_and_rewrite(
            "SELECT * FROM ai_view_transactions WHERE account_code = '5100'",
            household_id=HID,
        )
        assert "account_code = '5100'" in safe.sql


def test_schema_card_lists_ai_views() -> None:
    card = schema_card()
    assert "ai_view_transactions" in card
    assert "amount" in card
    assert "account_code" in card
