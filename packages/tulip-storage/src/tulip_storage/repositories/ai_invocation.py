"""AIInvocationRepository — household-scoped lifecycle ops on ai_invocations.

Row *creation* is the sole responsibility of
``tulip_ai.audit.AIInvocationWriter`` (ADR-0005 §Q6). This repository
carries the privacy-lifecycle operations that mutate existing rows —
currently the consent-withdrawal scrub (#243). The periodic TTL
collection lives in the ``ai_retention`` runner handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import update

from tulip_storage.models import AIInvocation

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


class AIInvocationRepository:
    """Household-scoped lifecycle operations over ai_invocations."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def scrub_prompt_logs(self) -> int:
        """Null ``prompt_json`` + ``response_text`` on every row in this household.

        Used when a household withdraws ``log_prompts`` consent (#243) —
        GDPR Art. 17(1)(b). The row, ``prompt_hash``, and the cost
        metadata survive for the audit chain; only the opt-in prompt /
        response bodies are erased. Returns the number of rows updated.
        """
        result = self._session.execute(
            update(AIInvocation)
            .where(AIInvocation.household_id == self._household_id)
            .values(prompt_json=None, response_text=None)
        )
        self._session.flush()
        # session.execute() on a bulk UPDATE returns a CursorResult at runtime;
        # the Session.execute signature only narrows to Result.
        return cast("CursorResult[Any]", result).rowcount or 0
