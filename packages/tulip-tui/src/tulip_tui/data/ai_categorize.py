"""TUI data adapter for ``POST /v1/ai/categorize-proposals`` (#425).

Thin wrapper around the AI top-N propose endpoint. The screen calls
this when the user presses ``c`` on a PENDING transaction; the
modal renders the returned candidates with the top-1 prominent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class AIProposalCandidate:
    """One ranked candidate from the API."""

    account_code: str
    confidence: float
    reasoning: str | None


def fetch_proposals(
    client: TulipClient,
    *,
    description: str,
    amount: Decimal,
    currency: str,
    posted_date: str,
    n: int = 5,
) -> tuple[AIProposalCandidate, ...]:
    """Call ``POST /v1/ai/categorize-proposals``; return the ranked list."""
    resp = client.post(
        "/v1/ai/categorize-proposals",
        authenticated=True,
        json={
            "description": description,
            "amount": str(amount),
            "currency": currency,
            "posted_date": posted_date,
            "n": n,
        },
    )
    payload = cast("dict[str, object]", resp.json())
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return ()
    out: list[AIProposalCandidate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        code = entry.get("account_code")
        conf = entry.get("confidence")
        if not isinstance(code, str) or not isinstance(conf, (int, float)):
            continue
        reasoning_raw = entry.get("reasoning")
        reasoning = reasoning_raw if isinstance(reasoning_raw, str) and reasoning_raw else None
        out.append(
            AIProposalCandidate(
                account_code=code,
                confidence=float(conf),
                reasoning=reasoning,
            )
        )
    return tuple(out)


__all__ = ["AIProposalCandidate", "fetch_proposals"]
