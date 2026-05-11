"""``AIInvocationWriter`` — the only path that INSERTs into ``ai_invocations`` (ADR-0005 §Q6).

Mirrors the writer-chokepoint pattern from ADR-0001 (shadow ledger):
one entry point, architecture test bans bypasses, every column documented.

Callers pass a fully-decided payload; the writer commits in the caller's
session so the row is part of the same transaction as whatever capability
work produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tulip_storage.models import AIInvocation

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def hash_prompt_payload(payload: dict[str, object]) -> bytes:
    """SHA-256 over a stable JSON encoding of the redacted prompt.

    ``ensure_ascii=False`` + ``sort_keys=True`` make the hash stable
    across Python builds. Used both to populate ``ai_invocations.prompt_hash``
    and to answer "was this exact prompt sent before" without storing
    prompts.
    """
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).digest()


@dataclass(slots=True)
class AIInvocationRecord:
    """Everything the writer needs to commit one row."""

    household_id: UUID
    capability: str
    policy_resolved: str
    profile: str
    outcome: str
    prompt_hash: bytes
    actor_user_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_usd: Decimal = Decimal("0")
    latency_ms: int = 0
    provider_response_id: str | None = None
    request_id: UUID | None = None
    prompt_json: str | None = None
    response_text: str | None = None
    proposal_id: UUID | None = None
    extra: dict[str, object] = field(default_factory=dict)


class AIInvocationWriter:
    """Sole entry point for ``ai_invocations`` INSERTs.

    The architecture test ``test_architecture_no_direct_ai_invocation_writes``
    asserts no other module constructs an ``AIInvocation``. Tests that need
    a row insert through this writer too — there's no test backdoor.
    """

    def __init__(self, session: Session) -> None:
        """Bind to a session; the caller owns transaction scope."""
        self._session = session

    def write(self, record: AIInvocationRecord) -> AIInvocation:
        """Commit one row; return the inserted ``AIInvocation``.

        Callers may use the returned row's ``id`` / ``created_at`` to link
        a proposal back to its invocation. The writer ``flush``es so the
        PK is populated; the caller owns ``session.commit()``.
        """
        row = AIInvocation(
            household_id=record.household_id,
            id=uuid4(),
            created_at=datetime.now(UTC),
            actor_user_id=record.actor_user_id,
            capability=record.capability,
            policy_resolved=record.policy_resolved,
            profile=record.profile,
            provider=record.provider,
            model=record.model,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            cost_estimate_usd=record.cost_estimate_usd,
            latency_ms=record.latency_ms,
            outcome=record.outcome,
            provider_response_id=record.provider_response_id,
            request_id=record.request_id,
            prompt_hash=record.prompt_hash,
            prompt_json=record.prompt_json,
            response_text=record.response_text,
            proposal_id=record.proposal_id,
        )
        self._session.add(row)
        self._session.flush()
        return row
