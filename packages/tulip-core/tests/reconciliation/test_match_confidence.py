"""Unit tests for MatchConfidence enum."""

from __future__ import annotations

from tulip_core.reconciliation import MatchConfidence


class TestMatchConfidenceValues:
    def test_three_values_only(self):
        assert {m.value for m in MatchConfidence} == {"high", "medium", "low"}

    def test_string_values_match_schema(self):
        # ADR-0004 §"Schema (P5.1 migration sketch)": the
        # reconciliation_matches.confidence column has CHECK IN
        # ('high','medium','low'). Values must match for the API to
        # round-trip without a converter.
        assert MatchConfidence.HIGH.value == "high"
        assert MatchConfidence.MEDIUM.value == "medium"
        assert MatchConfidence.LOW.value == "low"

    def test_str_mixin_serializes_to_value(self):
        # str.__str__ on a str-Enum returns the enum-repr in Py3.11+;
        # but the .value access is what the API serializer uses.
        assert MatchConfidence.HIGH.value == "high"

    def test_total_ordering(self):
        assert MatchConfidence.HIGH > MatchConfidence.MEDIUM
        assert MatchConfidence.MEDIUM > MatchConfidence.LOW
        assert MatchConfidence.HIGH > MatchConfidence.LOW

    def test_ordering_is_transitive(self):
        confidences = [
            MatchConfidence.LOW,
            MatchConfidence.HIGH,
            MatchConfidence.MEDIUM,
        ]
        assert sorted(confidences) == [
            MatchConfidence.LOW,
            MatchConfidence.MEDIUM,
            MatchConfidence.HIGH,
        ]

    def test_ordering_rejects_other_types(self):
        # Comparing to non-MatchConfidence is NotImplemented (raises TypeError
        # via Python's default behavior). String comparison would silently
        # work via the str mixin if we weren't careful — make sure it's not
        # silent.
        import pytest

        with pytest.raises(TypeError):
            _ = MatchConfidence.HIGH < "low"
