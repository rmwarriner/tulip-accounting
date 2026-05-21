"""Unit tests for the Categorizer Protocol + NullCategorizer + registry."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from uuid import uuid4

import pytest

from tulip_core.money import Money
from tulip_core.reconciliation import (
    CategorizationResult,
    Categorizer,
    HouseholdContext,
    NullCategorizer,
    StatementLine,
    get_categorizer,
    register_categorizer,
)
from tulip_core.reconciliation.categorizer import _reset_categorizer_for_testing


@pytest.fixture(autouse=True)
def _reset_registry():
    """Every test starts with the default registry (no custom registration)."""
    _reset_categorizer_for_testing()
    yield
    _reset_categorizer_for_testing()


def _line() -> StatementLine:
    return StatementLine(
        id=uuid4(),
        import_batch_id=uuid4(),
        line_number=1,
        posted_date=date(2026, 5, 12),
        amount=Money(Decimal("-42.17"), "USD"),
        description="Amazon Kindle",
        raw=MappingProxyType({}),
    )


def _ctx() -> HouseholdContext:
    return HouseholdContext(
        household_id=uuid4(),
        account_whitelist=frozenset(),
    )


# ---- CategorizationResult -------------------------------------------------


class TestCategorizationResult:
    def test_minimal_construction(self):
        r = CategorizationResult(account_code="Imbalance:Unknown", confidence=1.0)
        assert r.account_code == "Imbalance:Unknown"
        assert r.confidence == 1.0

    def test_frozen(self):
        r = CategorizationResult(account_code="x", confidence=0.5)
        with pytest.raises((AttributeError, TypeError)):
            r.confidence = 0.6  # type: ignore[misc]

    def test_account_code_non_empty(self):
        with pytest.raises(ValueError, match="account_code"):
            CategorizationResult(account_code="", confidence=1.0)

    def test_confidence_in_range(self):
        with pytest.raises(ValueError, match="confidence"):
            CategorizationResult(account_code="x", confidence=-0.1)
        with pytest.raises(ValueError, match="confidence"):
            CategorizationResult(account_code="x", confidence=1.01)

    def test_confidence_boundaries_allowed(self):
        CategorizationResult(account_code="x", confidence=0.0)
        CategorizationResult(account_code="x", confidence=1.0)


# ---- HouseholdContext -----------------------------------------------------


class TestHouseholdContext:
    def test_minimal_construction(self):
        ctx = HouseholdContext(
            household_id=uuid4(),
            account_whitelist=frozenset(),
        )
        assert ctx.existing_rules == ()

    def test_frozen(self):
        ctx = HouseholdContext(
            household_id=uuid4(),
            account_whitelist=frozenset(),
        )
        with pytest.raises((AttributeError, TypeError)):
            ctx.household_id = uuid4()  # type: ignore[misc]


# ---- Categorizer Protocol -------------------------------------------------


class TestCategorizerProtocol:
    def test_protocol_is_runtime_checkable(self):
        # NullCategorizer should pass isinstance(_, Categorizer).
        assert isinstance(NullCategorizer(), Categorizer)

    def test_arbitrary_object_fails_isinstance(self):
        # Concrete class without categorize() method.
        class NotACategorizer:
            pass

        assert not isinstance(NotACategorizer(), Categorizer)


# ---- NullCategorizer ------------------------------------------------------


class TestNullCategorizer:
    @pytest.mark.asyncio
    async def test_returns_imbalance_unknown(self):
        cat = NullCategorizer()
        result = await cat.categorize(_line(), _ctx())
        assert result.account_code == "Imbalance:Unknown"
        assert result.confidence == 1.0


# ---- registry -------------------------------------------------------------


class TestRegistry:
    def test_default_is_null_categorizer(self):
        c = get_categorizer()
        assert isinstance(c, NullCategorizer)

    def test_register_replaces_default(self):
        class CustomCategorizer:
            async def categorize(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                session: Any = None,
            ) -> CategorizationResult:
                return CategorizationResult(account_code="Custom:Account", confidence=0.5)

            async def propose(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                n: int = 5,
                session: Any = None,
            ):
                from tulip_core.reconciliation.categorizer import (
                    CategorizationCandidate,
                )

                return (CategorizationCandidate("Custom:Account", 0.5),)

        custom = CustomCategorizer()
        register_categorizer(custom)
        assert get_categorizer() is custom

    def test_register_rejects_non_categorizer(self):
        class NotACategorizer:
            pass

        with pytest.raises(TypeError, match="Categorizer"):
            register_categorizer(NotACategorizer())  # type: ignore[arg-type]

    def test_re_registration_warns(self):
        from tulip_core.reconciliation.categorizer import CategorizationCandidate

        class CustomA:
            async def categorize(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                session: Any = None,
            ) -> CategorizationResult:
                return CategorizationResult("A", 1.0)

            async def propose(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                n: int = 5,
                session: Any = None,
            ):
                return (CategorizationCandidate("A", 1.0),)

        class CustomB:
            async def categorize(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                session: Any = None,
            ) -> CategorizationResult:
                return CategorizationResult("B", 1.0)

            async def propose(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                n: int = 5,
                session: Any = None,
            ):
                return (CategorizationCandidate("B", 1.0),)

        register_categorizer(CustomA())
        with pytest.warns(UserWarning, match="replac"):
            register_categorizer(CustomB())
        assert isinstance(get_categorizer(), CustomB)

    @pytest.mark.asyncio
    async def test_async_protocol_enforced(self):
        # A "categorizer" with a sync categorize method should be rejected
        # at registration time. inspect.iscoroutinefunction is the check.
        class SyncCategorizer:
            def categorize(  # type: ignore[override]
                self, line: StatementLine, household_context: HouseholdContext
            ) -> CategorizationResult:
                return CategorizationResult("x", 1.0)

            async def propose(
                self,
                line: StatementLine,
                household_context: HouseholdContext,
                *,
                n: int = 5,
                session: Any = None,
            ):
                from tulip_core.reconciliation.categorizer import (
                    CategorizationCandidate,
                )

                return (CategorizationCandidate("x", 1.0),)

        with pytest.raises(TypeError, match="async"):
            register_categorizer(SyncCategorizer())  # type: ignore[arg-type]
