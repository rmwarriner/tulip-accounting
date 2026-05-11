"""Per-capability prompt payloads + the ``PromptRedactor`` (ADR-0005 §Q3, §Q4).

Three profiles:

* ``default`` — full payload as documented in ADR-0005 §Q3 (raw description,
  exact amount, recent examples).
* ``strict`` — token-redacted description, order-of-magnitude amount bucket,
  no recent examples.
* ``local_only`` — passes the payload through unchanged but asserts the
  resolved provider is local-only (Ollama). Caller is responsible for the
  provider check; this module only attests to the no-changes guarantee.

Redaction is a pure function over the payload dataclass; the byte-faithful
preview surface (``tulip ai preview``) calls the same path and asserts the
output is identical to what the live capability would send.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

RedactionProfile = Literal["default", "strict", "local_only"]


@dataclass(frozen=True, slots=True)
class ChartEntry:
    """One row of the household's chart of accounts, as the model sees it."""

    code: str
    name: str
    type: str  # asset / liability / equity / income / expense


@dataclass(frozen=True, slots=True)
class CategorizeExample:
    """One past-categorization example for few-shot prompting."""

    description: str
    code: str


@dataclass(frozen=True, slots=True)
class CategorizePromptPayload:
    """The exact shape the categorize capability sends.

    Order of fields is the order they appear in the JSON-encoded message
    body; tests assert byte-equality so this dataclass is the contract.
    """

    description: str
    amount: Decimal
    currency: str
    posted_date: str
    chart: tuple[ChartEntry, ...]
    recent_examples: tuple[CategorizeExample, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable view used by the adapter + the preview."""
        return {
            "task": "categorize",
            "line": {
                "description": self.description,
                "amount": str(self.amount),
                "currency": self.currency,
                "posted_date": self.posted_date,
            },
            "chart": [{"code": c.code, "name": c.name, "type": c.type} for c in self.chart],
            "recent_examples": [
                {"description": e.description, "code": e.code} for e in self.recent_examples
            ],
        }


_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
# Tokens too short to be discriminating; redaction keeps the structure but
# replaces the rest. The chosen 4-char threshold preserves things like
# "FUEL" and "AMZN" while collapsing vendor names with embedded common
# words.
_KEEP_MIN_LEN = 4
# Tokens that *are* category-discriminating regardless of length. Captures
# the common bank shorthand that's directly category-signal.
_KEEP_TOKENS = frozenset({"GAS", "ATM", "FEE", "TAX", "BAR", "DMV", "USPS"})


def _strict_redact_description(description: str) -> str:
    """Drop counterparty-identifying tokens; keep category-signal tokens.

    Short keepers (e.g. "GAS", "ATM") survive because they discriminate
    categories without naming a counterparty. Longer tokens survive by
    length — the goal is "name redacted, category signal preserved",
    not "perfect anonymization".
    """
    tokens: list[str] = []
    for tok in _TOKEN_SPLIT.split(description):
        if not tok:
            continue
        keep = tok.upper() in _KEEP_TOKENS or len(tok) >= _KEEP_MIN_LEN
        tokens.append(tok if keep else "*")
    return " ".join(tokens) if tokens else "(redacted)"


def _bucket_amount(amount: Decimal) -> str:
    """Order-of-magnitude bucket; preserves sign."""
    abs_amt = abs(amount)
    if abs_amt == 0:
        bucket = "0"
    else:
        magnitude = math.floor(math.log10(float(abs_amt)))
        low = 10**magnitude
        high = 10 ** (magnitude + 1)
        bucket = f"{low:g}-{high:g}"
    return f"-{bucket}" if amount < 0 else bucket


class PromptRedactor:
    """Strip / bucket fields per the per-capability contract."""

    def __init__(self, profile: RedactionProfile) -> None:
        """Bind the redactor to a profile.

        Profile is a runtime constant per call — a single redactor instance
        is reused for the duration of one capability invocation, but the
        profile itself doesn't change mid-call.
        """
        self._profile = profile

    @property
    def profile(self) -> RedactionProfile:
        """Return the active redaction profile."""
        return self._profile

    def redact_categorize(self, payload: CategorizePromptPayload) -> CategorizePromptPayload:
        """Apply the categorize-capability redaction rules.

        ``default`` and ``local_only`` profiles pass the payload through
        unchanged; ``strict`` rewrites the description, buckets the amount,
        and drops recent_examples per ADR-0005 §Q3.
        """
        if self._profile in ("default", "local_only"):
            return payload
        # strict — description is token-redacted; amount stays on the
        # dataclass for audit / cost-cap math; bucketed amount is
        # substituted into the message body in ``to_message_body``.
        return CategorizePromptPayload(
            description=_strict_redact_description(payload.description),
            amount=payload.amount,
            currency=payload.currency,
            posted_date=payload.posted_date,
            chart=payload.chart,
            recent_examples=(),
        )

    def to_message_body(self, payload: CategorizePromptPayload) -> dict[str, object]:
        """Return the JSON body sent to the provider, after profile rewrites.

        ``default`` / ``local_only`` use the payload's ``to_dict`` verbatim;
        ``strict`` substitutes the bucketed amount in the ``line.amount``
        field (the payload's Decimal is unchanged so the audit row can
        carry the bucket string while ``cost_estimate_usd`` math stays
        based on the real amount further up the call stack).
        """
        if self._profile in ("default", "local_only"):
            return payload.to_dict()
        body = self.redact_categorize(payload).to_dict()
        # Replace the line.amount with the bucket string; everything else
        # is already redacted-as-dataclass.
        line_raw = body["line"]
        assert isinstance(line_raw, dict)  # noqa: S101 - to_dict guarantee
        line_raw["amount"] = _bucket_amount(payload.amount)
        body["line"] = line_raw
        return body


__all__ = [
    "CategorizeExample",
    "CategorizePromptPayload",
    "ChartEntry",
    "PromptRedactor",
    "RedactionProfile",
]
