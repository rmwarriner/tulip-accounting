"""Unit tests for Account value object (core / structural)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tulip_core.account import Account, AccountType


class TestAccountConstruction:
    def test_constructs_with_required_fields(self):
        a = Account(
            id=uuid4(),
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=None,
        )
        assert a.name == "Checking"
        assert a.type is AccountType.ASSET

    def test_code_is_optional(self):
        a = Account(
            id=uuid4(),
            code=None,
            name="Petty Cash",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=None,
        )
        assert a.code is None

    def test_parent_id_optional(self):
        parent = uuid4()
        a = Account(
            id=uuid4(),
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=parent,
        )
        assert a.parent_id == parent

    def test_unknown_currency_raises(self):
        with pytest.raises(ValueError, match="ISO 4217"):
            Account(
                id=uuid4(),
                code=None,
                name="Mystery",
                type=AccountType.ASSET,
                currency="ZZZ",
                parent_id=None,
            )

    def test_invalid_code_format_raises(self):
        # Codes, when provided, must be non-empty and contain no whitespace.
        with pytest.raises(ValueError, match="code"):
            Account(
                id=uuid4(),
                code="",
                name="Bad",
                type=AccountType.ASSET,
                currency="USD",
                parent_id=None,
            )
        with pytest.raises(ValueError, match="code"):
            Account(
                id=uuid4(),
                code="11 10",
                name="Bad",
                type=AccountType.ASSET,
                currency="USD",
                parent_id=None,
            )


class TestAccountType:
    @pytest.mark.parametrize("name", ["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"])
    def test_all_canonical_types_exist(self, name: str):
        assert AccountType[name].name == name


class TestAccountEquality:
    def test_equality_is_by_id(self):
        same_id = uuid4()
        a = Account(
            id=same_id,
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=None,
        )
        b = Account(
            id=same_id,
            code="9999",  # different
            name="Different name",
            type=AccountType.LIABILITY,  # different
            currency="EUR",  # different
            parent_id=uuid4(),  # different
        )
        assert a == b

    def test_different_ids_not_equal(self):
        a = Account(
            id=uuid4(),
            code="1110",
            name="A",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=None,
        )
        b = Account(
            id=uuid4(),
            code="1110",
            name="A",
            type=AccountType.ASSET,
            currency="USD",
            parent_id=None,
        )
        assert a != b
