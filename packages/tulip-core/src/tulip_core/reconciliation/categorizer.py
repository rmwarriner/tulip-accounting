"""Categorizer Protocol + NullCategorizer + module-global registry (P5.3).

Per ADR-0004 §Implementation notes, the auto-categorization seam is a
DI hook: the matcher (and P5.4's reconciliation flow) call
``get_categorizer()`` which returns whichever ``Categorizer`` was
registered at app startup. v1 default is :class:`NullCategorizer` —
returns ``Imbalance:Unknown`` so unmatched statement lines have a
placeholder category until the user (or, in Phase 6, an
``AICategorizer``) assigns the real one.

Async-by-design: ``Categorizer.categorize`` is ``async def`` from day
one because Phase 6's ``AICategorizer`` will issue an LLM call.
Locking the Protocol shape now avoids a sync→async API break later.

The registry is a module-global mutable slot. Concurrency-unsafe
(single-process server). Tests reset the slot via the
``_reset_categorizer_for_testing()`` escape hatch from a
``conftest`` autouse fixture.
"""

from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from tulip_core.reconciliation.statement_line import StatementLine


@dataclass(frozen=True, slots=True)
class CategorizationResult:
    """One categorizer's verdict on a statement line."""

    account_code: str
    confidence: float

    def __post_init__(self) -> None:
        """Validate ``account_code`` non-empty + ``confidence`` range."""
        if not self.account_code or not self.account_code.strip():
            raise ValueError("account_code must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0] (got {self.confidence})")


@dataclass(frozen=True, slots=True)
class HouseholdContext:
    """Per-household context handed to the categorizer.

    Minimal v1 surface — Phase 6's AI categorizer will want richer
    inputs (recent transactions, learned rules, account preferences),
    and that's where ``HouseholdContext`` will grow. Adding fields with
    defaults won't break existing callers.

    ``acting_user_id`` (#239) is the optional id of the user on whose
    behalf the categorization runs. When set, downstream AI capabilities
    read that user's ``ai_policy`` and per-user keys; when ``None`` the
    household-level policy is dispositive.
    """

    household_id: UUID
    account_whitelist: frozenset[UUID]
    existing_rules: tuple[object, ...] = field(default=())
    acting_user_id: UUID | None = None


@runtime_checkable
class Categorizer(Protocol):
    """Protocol for objects that suggest categories for unmatched statement lines.

    Phase 6's ``AICategorizer`` plugs in here via :func:`register_categorizer`.
    The matcher and P5.4's reconciliation flow call :func:`get_categorizer`
    to obtain the active implementation.

    ``session`` is the opt-in session-sharing hook (#199, #200): callers
    that are inside an open DB transaction pass theirs, and the concrete
    implementer (e.g. ``AICategorizer``) uses it so the audit row write
    can't deadlock against the caller's write lock. The Protocol stays
    DB-agnostic by typing it as ``object | None``; the concrete impl
    narrows to its own session type.
    """

    async def categorize(
        self,
        line: StatementLine,
        household_context: HouseholdContext,
        *,
        session: Any = None,  # noqa: ANN401 — tulip-core can't import sqlalchemy
    ) -> CategorizationResult:
        """Suggest a category for an unmatched statement line."""
        ...


class NullCategorizer:
    """v1 default: every line gets ``Imbalance:Unknown`` with confidence 1.0.

    "Unknown" is the honest answer in v1 — the user assigns the real
    category during reconciliation review. Phase 6's ``AICategorizer``
    replaces this with a real proposal.
    """

    async def categorize(
        self,
        line: StatementLine,
        household_context: HouseholdContext,
        *,
        session: Any = None,  # noqa: ANN401 — tulip-core can't import sqlalchemy
    ) -> CategorizationResult:
        """Return ``Imbalance:Unknown`` regardless of ``line``."""
        del line, household_context, session  # unused — placeholder-by-design
        return CategorizationResult(account_code="Imbalance:Unknown", confidence=1.0)


# ---- module-global registry -----------------------------------------------

_REGISTERED: Categorizer | None = None


def register_categorizer(categorizer: Categorizer) -> None:
    """Register a ``Categorizer`` to be returned by :func:`get_categorizer`.

    Re-registering replaces the previous categorizer and emits a
    ``UserWarning`` so accidental double-registration in production is
    visible in logs. Tests use :func:`_reset_categorizer_for_testing`
    to silently clear between cases.

    Raises:
        TypeError: ``categorizer`` doesn't satisfy the Protocol, or its
            ``categorize`` method is not ``async def``.

    """
    global _REGISTERED

    if not isinstance(categorizer, Categorizer):
        raise TypeError(
            "register_categorizer requires a Categorizer Protocol "
            f"implementer (got {type(categorizer).__name__}); the object "
            "must define `async def categorize(line, household_context)`"
        )
    if not inspect.iscoroutinefunction(categorizer.categorize):
        raise TypeError("Categorizer.categorize must be async (defined with `async def`)")
    if _REGISTERED is not None:
        warnings.warn(
            f"register_categorizer replacing existing {type(_REGISTERED).__name__} "
            f"with {type(categorizer).__name__} — accidental double-registration "
            "may indicate a startup-ordering bug.",
            UserWarning,
            stacklevel=2,
        )
    _REGISTERED = categorizer


def get_categorizer() -> Categorizer:
    """Return the registered categorizer, or a default :class:`NullCategorizer`."""
    if _REGISTERED is None:
        return NullCategorizer()
    return _REGISTERED


def _reset_categorizer_for_testing() -> None:
    """Clear the registry. Test-only — never call from production code."""
    global _REGISTERED
    _REGISTERED = None
