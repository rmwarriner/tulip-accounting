"""Domain errors raised from ``tulip_ai`` capabilities (ADR-0005)."""

from __future__ import annotations


class AIError(Exception):
    """Base for everything ``tulip_ai`` raises."""


class AICapDisabled(AIError):
    """The resolved policy is ``disabled`` for this capability.

    Raised before any provider call so that the audit row records
    ``outcome=policy_disabled`` and no PII leaves the local boundary.
    """


class AIProviderError(AIError):
    """The provider returned an error (HTTP, timeout, or malformed body)."""


class AIRateLimited(AIError):
    """This user hit the per-user sliding-window rate limit."""


class AICostCapped(AIError):
    """This household hit the configured monthly cost cap.

    Per ADR-0005 §Q7, the household's ``cost_cap_behaviour`` setting
    determines what happens next: ``degrade`` swaps to the local
    provider, ``hard_fail`` propagates this exception to the caller.
    """
