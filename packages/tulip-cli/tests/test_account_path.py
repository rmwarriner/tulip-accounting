"""Unit tests for ``_account_path`` (closes #300, #416).

The helpers replace the ``code:name`` formatter in ``transactions.py``
(#214) with full hierarchical paths everywhere a human reads an
account label. Round-trip with the input-side resolver
(``_match_name_or_path``, #197) is the key contract — these tests
pin both directions: paths rendered out can be typed back in.

Per #300's principle: paths replace UUIDs in *human* output;
machine output (``--json``) keeps UUIDs and isn't touched here.
"""

from __future__ import annotations

import pytest

from tulip_cli._account_path import (
    account_path,
    escape_segment,
    split_path,
    unescape_segment,
)


def _three_level_chart() -> dict[str, dict[str, str | None]]:
    """Assets → Current Assets → Checking. Standard happy-path fixture."""
    return {
        "root": {
            "id": "root",
            "name": "Assets",
            "type": "asset",
            "parent_account_id": None,
        },
        "mid": {
            "id": "mid",
            "name": "Current Assets",
            "type": "asset",
            "parent_account_id": "root",
        },
        "leaf": {
            "id": "leaf",
            "name": "Checking",
            "type": "asset",
            "parent_account_id": "mid",
        },
    }


class TestAccountPath:
    def test_walks_parent_chain_to_root(self) -> None:
        assert account_path("leaf", _three_level_chart()) == "Asset:Assets:Current Assets:Checking"

    def test_single_level_account_renders_type_then_name(self) -> None:
        chart = {
            "root": {
                "id": "root",
                "name": "Cash",
                "type": "asset",
                "parent_account_id": None,
            }
        }
        assert account_path("root", chart) == "Asset:Cash"

    def test_type_prefix_is_title_case_for_each_stored_type(self) -> None:
        for stored, displayed in (
            ("asset", "Asset"),
            ("liability", "Liability"),
            ("equity", "Equity"),
            ("income", "Income"),
            ("expense", "Expense"),
        ):
            chart = {
                "a": {
                    "id": "a",
                    "name": "Sample",
                    "type": stored,
                    "parent_account_id": None,
                }
            }
            assert account_path("a", chart) == f"{displayed}:Sample"

    def test_missing_account_renders_raw_uuid(self) -> None:
        # Orphaned posting reference — graceful degrade rather than
        # crash. Matches #214's ``_format_account_label`` precedent.
        uuid = "00000000-0000-0000-0000-000000000000"
        assert account_path(uuid, {}) == uuid

    def test_missing_parent_renders_question_mark_marker(self) -> None:
        # Leaf's parent_account_id points at "mid" but "mid" isn't in
        # the map. Render the gap as ``?`` so partial corruption is
        # visible rather than silently skipped.
        chart = {
            "leaf": {
                "id": "leaf",
                "name": "Checking",
                "type": "asset",
                "parent_account_id": "mid",
            }
        }
        assert account_path("leaf", chart) == "Asset:?:Checking"

    def test_name_containing_colon_is_escaped(self) -> None:
        # ``Imbalance:Unknown`` is the literal name of the
        # no-categorize bucket — see
        # ``tulip_api.services.import_apply._IMBALANCE_NAME``. Render
        # it as a single escaped segment so the path round-trips
        # through the resolver.
        chart = {
            "a": {
                "id": "a",
                "name": "Imbalance:Unknown",
                "type": "equity",
                "parent_account_id": None,
            }
        }
        assert account_path("a", chart) == r"Equity:Imbalance\:Unknown"

    def test_name_containing_backslash_is_escaped(self) -> None:
        chart = {
            "a": {
                "id": "a",
                "name": r"foo\bar",
                "type": "asset",
                "parent_account_id": None,
            }
        }
        assert account_path("a", chart) == r"Asset:foo\\bar"

    def test_cycle_breaks_the_walk(self) -> None:
        # Defensive: server enforces tree, but a malformed response
        # shouldn't hang the CLI. parent_account_id points back at
        # self → walk stops at the second visit.
        chart = {
            "a": {
                "id": "a",
                "name": "loop",
                "type": "asset",
                "parent_account_id": "a",
            }
        }
        assert account_path("a", chart) == "Asset:loop"

    def test_unknown_type_falls_back_to_capitalized(self) -> None:
        # Defensive: API could grow new account types ahead of the
        # CLI's TYPE_DISPLAY table.
        chart = {
            "a": {
                "id": "a",
                "name": "Crypto",
                "type": "wallet",
                "parent_account_id": None,
            }
        }
        assert account_path("a", chart) == "Wallet:Crypto"


class TestEscapeUnescape:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain", "plain"),
            ("has:colon", r"has\:colon"),
            (r"has\backslash", r"has\\backslash"),
            (r"both\:and", r"both\\\:and"),
            ("", ""),
        ],
    )
    def test_escape_segment(self, raw: str, expected: str) -> None:
        assert escape_segment(raw) == expected

    @pytest.mark.parametrize(
        ("escaped", "expected"),
        [
            ("plain", "plain"),
            (r"has\:colon", "has:colon"),
            (r"has\\backslash", r"has\backslash"),
            (r"both\\\:and", r"both\:and"),
            ("", ""),
        ],
    )
    def test_unescape_segment(self, escaped: str, expected: str) -> None:
        assert unescape_segment(escaped) == expected

    def test_round_trip(self) -> None:
        for raw in ("plain", "has:colon", r"has\backslash", r"both\:and"):
            assert unescape_segment(escape_segment(raw)) == raw


class TestSplitPath:
    def test_simple_path(self) -> None:
        assert split_path("Asset:Cash:Checking") == ["Asset", "Cash", "Checking"]

    def test_escaped_colon_keeps_segment_whole(self) -> None:
        assert split_path(r"Equity:Imbalance\:Unknown") == [
            "Equity",
            "Imbalance:Unknown",
        ]

    def test_escaped_backslash(self) -> None:
        assert split_path(r"Asset:foo\\bar") == ["Asset", r"foo\bar"]

    def test_strips_whitespace_within_segments(self) -> None:
        assert split_path("Asset : Current Assets : Checking") == [
            "Asset",
            "Current Assets",
            "Checking",
        ]

    def test_empty_middle_segment_returns_none(self) -> None:
        assert split_path("Asset::Checking") is None

    def test_trailing_colon_returns_none(self) -> None:
        assert split_path("Asset:Checking:") is None

    def test_leading_colon_returns_none(self) -> None:
        assert split_path(":Checking") is None

    def test_single_segment(self) -> None:
        assert split_path("Cash") == ["Cash"]

    def test_round_trip_with_escaped_names(self) -> None:
        # Rendered path: escape per segment, join on ``:``. Resolver
        # then split_path → unescaped segments must equal originals.
        names = ["Imbalance:Unknown", r"foo\bar", "Plain"]
        rendered = ":".join(escape_segment(n) for n in names)
        parsed = split_path(rendered)
        assert parsed == names
