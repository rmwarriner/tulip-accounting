"""Tests for the LitellmAdapter privacy / safety pinning (#248).

ADR-0005 promises "AI is the only egress, and only when you explicitly
use it." litellm's package-default for ``telemetry`` is ``True`` — a
startup version-check against PyPI — and the library exposes optional
callback hooks that could be enabled by a future package default. These
tests assert that instantiating ``LitellmAdapter`` actively pins every
known surface off, so a litellm upgrade can't silently introduce an
egress without a Tulip code change.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def reset_litellm_defaults():
    """Restore litellm's module-level flags around each test.

    The adapter pins flags as a side effect, so we explicitly reset to
    litellm's "natural" defaults before each test and restore whatever
    was there after. Without this, test order would leak state across
    workers and the "before" assertion in the pin test would flake.
    """
    import litellm

    before = {
        "telemetry": getattr(litellm, "telemetry", None),
        "success_callback": list(getattr(litellm, "success_callback", []) or []),
        "failure_callback": list(getattr(litellm, "failure_callback", []) or []),
        "callbacks": list(getattr(litellm, "callbacks", []) or []),
        "suppress_debug_info": getattr(litellm, "suppress_debug_info", None),
    }
    try:
        yield
    finally:
        for name, value in before.items():
            setattr(litellm, name, value)


class TestLitellmAdapterPinning:
    def test_instantiation_pins_telemetry_off(self, reset_litellm_defaults) -> None:
        """litellm.telemetry defaults to True — Tulip must flip it off (#248)."""
        import litellm

        from tulip_ai.adapters import LitellmAdapter

        # Simulate a "fresh" environment where telemetry was on.
        litellm.telemetry = True
        LitellmAdapter()
        assert litellm.telemetry is False

    def test_instantiation_clears_callbacks(self, reset_litellm_defaults) -> None:
        """Pre-existing callbacks must be cleared so an upgrade can't silently
        seed a non-empty default that begins egressing.
        """
        import litellm

        from tulip_ai.adapters import LitellmAdapter

        litellm.success_callback = ["fake.success"]
        litellm.failure_callback = ["fake.failure"]
        litellm.callbacks = ["fake.callback"]
        LitellmAdapter()
        assert litellm.success_callback == []
        assert litellm.failure_callback == []
        assert litellm.callbacks == []

    def test_instantiation_suppresses_debug_info(self, reset_litellm_defaults) -> None:
        """suppress_debug_info defaults to False; pinning True keeps banners
        + version-warnings off in case the library prints anything sensitive.
        """
        import litellm

        from tulip_ai.adapters import LitellmAdapter

        litellm.suppress_debug_info = False
        LitellmAdapter()
        assert litellm.suppress_debug_info is True

    def test_pin_is_idempotent(self, reset_litellm_defaults) -> None:
        """Multiple LitellmAdapter() instances all leave the flags pinned."""
        import litellm

        from tulip_ai.adapters import LitellmAdapter

        LitellmAdapter()
        LitellmAdapter()
        LitellmAdapter()
        assert litellm.telemetry is False
        assert litellm.success_callback == []
        assert litellm.failure_callback == []
        assert litellm.callbacks == []
        assert litellm.suppress_debug_info is True
