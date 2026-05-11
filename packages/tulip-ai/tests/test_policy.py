"""Unit tests for ``resolve_policy`` (ADR-0005 §Q5)."""

from __future__ import annotations

from decimal import Decimal

from tulip_ai.policy import resolve_policy


class TestSeverityRatchet:
    def test_household_floor_applies_when_user_unset(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "requires_approval"}}}
        r = resolve_policy(h, None, "categorize")
        assert r.level == "requires_approval"

    def test_user_can_ratchet_up_from_permissive_to_requires_approval(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "permissive"}}}
        u = {"capabilities": {"categorize": {"policy": "requires_approval"}}}
        r = resolve_policy(h, u, "categorize")
        assert r.level == "requires_approval"

    def test_user_cannot_ratchet_down_from_requires_approval(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "requires_approval"}}}
        u = {"capabilities": {"categorize": {"policy": "permissive"}}}
        r = resolve_policy(h, u, "categorize")
        assert r.level == "requires_approval"

    def test_user_disabled_wins_over_household_permissive(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "permissive"}}}
        u = {"capabilities": {"categorize": {"policy": "disabled"}}}
        r = resolve_policy(h, u, "categorize")
        assert r.level == "disabled"

    def test_household_disabled_wins_over_user_permissive(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "disabled"}}}
        u = {"capabilities": {"categorize": {"policy": "permissive"}}}
        r = resolve_policy(h, u, "categorize")
        assert r.level == "disabled"


class TestDefaults:
    def test_empty_policy_resolves_to_permissive(self) -> None:
        r = resolve_policy({}, None, "categorize")
        assert r.level == "permissive"
        assert r.profile == "default"
        assert r.provider is None
        assert r.monthly_cost_cap_usd is None

    def test_garbage_level_string_falls_through_to_default(self) -> None:
        h = {"capabilities": {"categorize": {"policy": "nonsense"}}}
        r = resolve_policy(h, None, "categorize")
        assert r.level == "permissive"


class TestProviderInheritance:
    def test_household_default_provider_inherited(self) -> None:
        h = {
            "default_provider": "anthropic",
            "default_model": "claude-opus-4-7",
        }
        r = resolve_policy(h, None, "categorize")
        assert r.provider == "anthropic"
        assert r.model == "claude-opus-4-7"

    def test_capability_override_wins_over_household_default(self) -> None:
        h = {
            "default_provider": "anthropic",
            "capabilities": {"categorize": {"provider": "openai", "model": "gpt-5"}},
        }
        r = resolve_policy(h, None, "categorize")
        assert r.provider == "openai"
        assert r.model == "gpt-5"

    def test_local_only_profile_forces_ollama(self) -> None:
        h = {
            "default_provider": "anthropic",
            "fallback_provider": "ollama",
            "fallback_model": "llama3:70b",
            "profile": "local_only",
        }
        r = resolve_policy(h, None, "categorize")
        assert r.profile == "local_only"
        assert r.provider == "ollama"
        assert r.model == "llama3:70b"


class TestCostCap:
    def test_cap_parsed_as_decimal(self) -> None:
        h = {"monthly_cost_cap_usd": "10.00"}
        r = resolve_policy(h, None, "categorize")
        assert r.monthly_cost_cap_usd == Decimal("10.00")

    def test_garbage_cap_is_none(self) -> None:
        h = {"monthly_cost_cap_usd": "not a number"}
        r = resolve_policy(h, None, "categorize")
        assert r.monthly_cost_cap_usd is None
