"""Policy resolution: household-floor + user-ratchet-up (ADR-0005 §Q5).

The household's ``ai_policy`` is the floor. Per-user settings can ratchet
*up* (more cautious than the household says) but cannot ratchet *down*.
Severity ordering: ``disabled`` > ``requires_approval`` > ``permissive``.

The household policy shape lives in ARCHITECTURE.md §6.5. Missing fields
are filled with code defaults — a fresh install has ``ai_policy = {}``
which resolves to "permissive across all capabilities, no provider, no
cost cap, no fallback". Operators opt in by editing the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from tulip_ai.redaction import RedactionProfile

PolicyLevel = Literal["permissive", "requires_approval", "disabled"]
Capability = Literal["categorize", "nl_query", "forecast", "agentic"]
CostCapBehaviour = Literal["degrade", "hard_fail"]

_SEVERITY: dict[PolicyLevel, int] = {
    "permissive": 0,
    "requires_approval": 1,
    "disabled": 2,
}
_VALID_LEVELS = frozenset(_SEVERITY.keys())


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    """Effective policy for one (household, user, capability) tuple."""

    capability: Capability
    level: PolicyLevel
    provider: str | None
    model: str | None
    profile: RedactionProfile
    monthly_cost_cap_usd: Decimal | None
    cost_cap_behaviour: CostCapBehaviour
    rate_limit_per_hour: int
    fallback_provider: str | None
    fallback_model: str | None
    log_prompts: bool


def _coerce_level(value: object, default: PolicyLevel) -> PolicyLevel:
    if value == "permissive":
        return "permissive"
    if value == "requires_approval":
        return "requires_approval"
    if value == "disabled":
        return "disabled"
    return default


def _coerce_profile(value: object, default: RedactionProfile) -> RedactionProfile:
    if value == "default":
        return "default"
    if value == "strict":
        return "strict"
    if value == "local_only":
        return "local_only"
    return default


def _coerce_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _coerce_cost_cap_behaviour(value: object, default: CostCapBehaviour) -> CostCapBehaviour:
    if value == "degrade":
        return "degrade"
    if value == "hard_fail":
        return "hard_fail"
    return default


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def resolve_policy(
    household_policy: dict[str, Any],
    user_policy: dict[str, Any] | None,
    capability: Capability,
) -> ResolvedPolicy:
    """Return the effective policy for ``capability`` for this user.

    The resolved level is the *max* severity between household and user.
    Provider / model are inherited from household defaults unless the
    capability has an override; users do not override provider / model.

    The profile defaults to ``default`` unless the household sets one
    per capability or globally. ``local_only`` overrides everything and
    pins the resolved provider to ``ollama`` regardless of other config.
    """
    capabilities = household_policy.get("capabilities") or {}
    cap_settings = capabilities.get(capability) or {}

    household_level = _coerce_level(cap_settings.get("policy"), "permissive")

    user_level: PolicyLevel = "permissive"
    if user_policy:
        user_caps = user_policy.get("capabilities") or {}
        user_cap = user_caps.get(capability) or {}
        user_level = _coerce_level(user_cap.get("policy"), "permissive")

    resolved_level: PolicyLevel
    if _SEVERITY[user_level] > _SEVERITY[household_level]:
        resolved_level = user_level
    else:
        resolved_level = household_level

    profile = _coerce_profile(
        cap_settings.get("profile") or household_policy.get("profile"),
        "default",
    )

    provider = cap_settings.get("provider") or household_policy.get("default_provider")
    model = cap_settings.get("model") or household_policy.get("default_model")

    if profile == "local_only":
        provider = household_policy.get("fallback_provider") or "ollama"
        model = household_policy.get("fallback_model") or model

    from tulip_ai.cost import DEFAULT_RATE_LIMIT_PER_HOUR

    return ResolvedPolicy(
        capability=capability,
        level=resolved_level,
        provider=provider if isinstance(provider, str) else None,
        model=model if isinstance(model, str) else None,
        profile=profile,
        monthly_cost_cap_usd=_coerce_decimal(household_policy.get("monthly_cost_cap_usd")),
        cost_cap_behaviour=_coerce_cost_cap_behaviour(
            household_policy.get("cost_cap_behaviour"), "degrade"
        ),
        rate_limit_per_hour=_coerce_positive_int(
            household_policy.get("rate_limit_per_hour"), DEFAULT_RATE_LIMIT_PER_HOUR
        ),
        fallback_provider=(
            household_policy.get("fallback_provider")
            if isinstance(household_policy.get("fallback_provider"), str)
            else None
        ),
        fallback_model=(
            household_policy.get("fallback_model")
            if isinstance(household_policy.get("fallback_model"), str)
            else None
        ),
        log_prompts=bool(household_policy.get("log_prompts", False)),
    )
