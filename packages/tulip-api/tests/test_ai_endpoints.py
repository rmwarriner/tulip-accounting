"""Tests for ``/v1/ai/...`` endpoints (P6.1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_token(client: TestClient) -> str:
    client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": "Smith",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )
    return str(r.json()["access_token"])


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


class TestKeys:
    def test_set_then_list_round_trip(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-test"})
        assert r.status_code == 204, r.text
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert listing.json()["providers"] == ["anthropic"]

    def test_keys_not_exposed_in_listing(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """``list-keys`` returns provider names only — never the key bytes."""
        client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-very-secret"})
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert "sk-very-secret" not in listing.text

    def test_forget_key_removes_provider(self, client: TestClient, auth_h: dict[str, str]) -> None:
        client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-test"})
        r = client.delete("/v1/ai/keys/anthropic", headers=auth_h)
        assert r.status_code == 204
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert listing.json()["providers"] == []

    def test_forget_unknown_provider_is_idempotent(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.delete("/v1/ai/keys/never-set", headers=auth_h)
        assert r.status_code == 204

    def test_keys_endpoints_require_admin(self, client: TestClient) -> None:
        r = client.post("/v1/ai/keys/anthropic", json={"api_key": "x"})
        assert r.status_code == 401


class TestStatus:
    def test_status_for_fresh_household_returns_defaults(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = client.get("/v1/ai/status", headers=auth_h).json()
        assert body["default_provider"] is None
        assert body["providers_with_keys"] == []
        # All four capabilities present.
        assert set(body["capabilities"].keys()) == {
            "categorize",
            "nl_query",
            "forecast",
            "agentic",
        }
        assert body["capabilities"]["categorize"]["level"] == "permissive"


class TestPreview:
    def test_preview_returns_byte_faithful_payload(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5100", "name": "Groceries", "type": "expense", "currency": "USD"},
        )
        client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5300", "name": "Fuel", "type": "expense", "currency": "USD"},
        )

        r = client.post(
            "/v1/ai/preview",
            headers=auth_h,
            json={
                "description": "WHOLE FOODS MARKET",
                "amount": "-87.42",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["profile"] == "default"
        payload = body["payload"]
        assert payload["task"] == "categorize"
        assert payload["line"]["description"] == "WHOLE FOODS MARKET"
        assert payload["line"]["amount"] == "-87.42"
        codes = sorted(c["code"] for c in payload["chart"])
        assert codes == ["5100", "5300"]

    def test_preview_requires_admin(self, client: TestClient) -> None:
        r = client.post(
            "/v1/ai/preview",
            json={
                "description": "X",
                "amount": "-1.00",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 401


class TestCategorizeProposals:
    """#425: top-N propose surface for the TUI."""

    def test_proposals_with_disabled_policy_returns_imbalance(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Fresh household has the default policy and no key → fallback."""
        client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5100", "name": "Groceries", "type": "expense", "currency": "USD"},
        )
        r = client.post(
            "/v1/ai/categorize-proposals",
            headers=auth_h,
            json={
                "description": "WHOLE FOODS MARKET",
                "amount": "-87.42",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["candidates"], list)
        # Without configured AI, the fallback single candidate fires.
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["account_code"] == "Imbalance:Unknown"

    def test_proposals_request_caps_n_field(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """n must be in [1, 10]."""
        r = client.post(
            "/v1/ai/categorize-proposals",
            headers=auth_h,
            json={
                "description": "X",
                "amount": "-1.00",
                "currency": "USD",
                "posted_date": "2026-05-03",
                "n": 20,
            },
        )
        assert r.status_code == 422

    def test_proposals_requires_auth(self, client: TestClient) -> None:
        r = client.post(
            "/v1/ai/categorize-proposals",
            json={
                "description": "X",
                "amount": "-1.00",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 401


class TestAsk:
    def test_ask_with_no_api_key_returns_error_summary(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Fresh household, no key → structured ``error`` field, not a 5xx."""
        r = client.post(
            "/v1/ai/ask",
            headers=auth_h,
            json={"question": "How much did I spend on groceries?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] == ""
        assert body["rows"] == []
        assert "no ai key" in (body["error"] or "").lower()

    def test_ask_with_disabled_policy_returns_error(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Policy ``disabled`` on nl_query → no provider call, structured error."""
        # Seed an API key + a disabled-nl_query policy.
        client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-test"})
        # Edit ai_policy via raw DB — there's no endpoint for it yet.
        from tulip_api.config import get_settings
        from tulip_api.deps import get_session

        overrides = client.app.dependency_overrides
        session_factory = overrides[get_session]
        with next(session_factory()) as session:
            from tulip_storage.models import Household

            settings = overrides[get_settings]()
            from sqlalchemy import select

            household = session.execute(select(Household)).scalar_one()
            household.ai_policy = {
                "default_provider": "anthropic",
                "default_model": "claude-opus-4-7",
                "capabilities": {"nl_query": {"policy": "disabled"}},
            }
            session.commit()
            _ = settings  # silence unused-warning

        r = client.post(
            "/v1/ai/ask",
            headers=auth_h,
            json={"question": "Anything?"},
        )
        body = r.json()
        assert "disabled" in (body["error"] or "").lower()

    def test_ask_requires_auth(self, client: TestClient) -> None:
        r = client.post("/v1/ai/ask", json={"question": "X"})
        assert r.status_code == 401


# --- P6.5.b: /v1/ai/config -----------------------------------------------


class TestConfigShow:
    def test_fresh_household_returns_defaults(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = client.get("/v1/ai/config", headers=auth_h).json()
        assert body["default_provider"] is None
        assert body["default_model"] is None
        assert body["cost_cap_behaviour"] == "degrade"
        assert body["rate_limit_per_hour"] == 60
        assert body["log_prompts"] is False
        for cap in ("categorize", "nl_query", "forecast", "agentic"):
            assert body["capabilities"][cap] == {
                "policy": None,
                "provider": None,
                "model": None,
                "profile": None,
            }

    def test_requires_admin(self, client: TestClient) -> None:
        r = client.get("/v1/ai/config")
        assert r.status_code == 401


class TestConfigPut:
    def test_sets_default_provider(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put("/v1/ai/config", headers=auth_h, json={"default_provider": "anthropic"})
        assert r.status_code == 200, r.text
        body = client.get("/v1/ai/config", headers=auth_h).json()
        assert body["default_provider"] == "anthropic"

    def test_config_exposes_invocation_retention_days(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """#243: the retention policy is surfaced read-only on the config."""
        from tulip_storage.runner.handlers import AI_INVOCATION_RETENTION_DAYS

        body = client.get("/v1/ai/config", headers=auth_h).json()
        assert body["invocation_retention_days"] == AI_INVOCATION_RETENTION_DAYS

    def test_withdrawing_log_prompts_scrubs_prompt_logs(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """#243: flipping log_prompts true->false nulls prompt_json + response_text."""
        from uuid import uuid4

        from sqlalchemy import select

        from tulip_storage.models import AIInvocation, AuditLog, Household

        client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": True}).raise_for_status()
        with session_maker() as s:
            household_id = s.execute(select(Household)).scalar_one().id
            for _ in range(2):
                s.add(
                    AIInvocation(
                        household_id=household_id,
                        id=uuid4(),
                        capability="nl_query",
                        policy_resolved="permissive",
                        profile="default",
                        outcome="success",
                        prompt_hash=b"\x00" * 32,
                        prompt_json='{"q": "secret question"}',
                        response_text="secret answer",
                    )
                )
            s.commit()

        r = client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": False})
        assert r.status_code == 200, r.text
        assert r.json()["log_prompts"] is False

        with session_maker() as s:
            rows = list(s.execute(select(AIInvocation)).scalars().all())
            audit = list(
                s.execute(select(AuditLog).where(AuditLog.action == "ai.prompt_log_scrubbed"))
                .scalars()
                .all()
            )
        assert len(rows) == 2
        # Bodies erased; the row + prompt_hash survive for the audit chain.
        assert all(row.prompt_json is None and row.response_text is None for row in rows)
        assert all(row.prompt_hash == b"\x00" * 32 for row in rows)
        assert len(audit) == 1
        assert audit[0].after_snapshot["rows_scrubbed"] == 2

    def test_put_config_writes_consent_changed_audit_row(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """#247: any mutation through PUT /v1/ai/config records an audit row.

        Carries full before/after of the household ``ai_policy`` blob and
        ``actor_user_id`` so GDPR Art. 7(1) "when did consent change and
        by whom" is answerable.
        """
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": True}).raise_for_status()

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "ai.consent_changed"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_type == "household"
        assert row.actor_user_id is not None
        # Empty before — the household was newly registered with default policy.
        assert row.before_snapshot == {}
        assert row.after_snapshot == {"log_prompts": True}

    def test_put_capability_config_writes_consent_changed_audit_row(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """The per-capability PUT also records a consent audit row (#247)."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        r = client.put(
            "/v1/ai/config/capabilities/nl_query",
            headers=auth_h,
            json={"policy": "disabled"},
        )
        assert r.status_code == 200, r.text

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "ai.consent_changed"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].before_snapshot == {}
        assert rows[0].after_snapshot == {
            "capabilities": {"nl_query": {"policy": "disabled"}},
        }

    def test_put_config_no_change_emits_no_audit_row(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """A no-op PUT (every field matches existing state) writes no row."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        # Default household ai_policy is {} — sending a body of {} is a no-op.
        client.put("/v1/ai/config", headers=auth_h, json={}).raise_for_status()

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "ai.consent_changed"))
                .scalars()
                .all()
            )
        assert rows == []

    def test_consent_audit_captures_log_prompts_flip(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """Toggling log_prompts on then off writes two distinct audit rows."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": True}).raise_for_status()
        client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": False}).raise_for_status()

        with session_maker() as s:
            rows = list(
                s.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "ai.consent_changed")
                    .order_by(AuditLog.occurred_at)
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert rows[0].before_snapshot == {}
        assert rows[0].after_snapshot == {"log_prompts": True}
        assert rows[1].before_snapshot == {"log_prompts": True}
        assert rows[1].after_snapshot == {"log_prompts": False}

    def test_no_scrub_when_log_prompts_not_a_true_to_false_transition(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """The scrub only fires on the true->false edge — not on a false->false PUT."""
        from uuid import uuid4

        from sqlalchemy import select

        from tulip_storage.models import AIInvocation, Household

        with session_maker() as s:
            household_id = s.execute(select(Household)).scalar_one().id
            s.add(
                AIInvocation(
                    household_id=household_id,
                    id=uuid4(),
                    capability="nl_query",
                    policy_resolved="permissive",
                    profile="default",
                    outcome="success",
                    prompt_hash=b"\x00" * 32,
                    prompt_json='{"q": "kept"}',
                    response_text="kept",
                )
            )
            s.commit()

        # Default policy has no log_prompts → this PUT is a false->false no-op.
        client.put("/v1/ai/config", headers=auth_h, json={"log_prompts": False}).raise_for_status()

        with session_maker() as s:
            row = s.execute(select(AIInvocation)).scalar_one()
        assert row.prompt_json == '{"q": "kept"}'
        assert row.response_text == "kept"

    def test_clears_with_sentinel(self, client: TestClient, auth_h: dict[str, str]) -> None:
        client.put("/v1/ai/config", headers=auth_h, json={"default_provider": "anthropic"})
        r = client.put("/v1/ai/config", headers=auth_h, json={"default_provider": "__CLEAR__"})
        assert r.status_code == 200
        body = client.get("/v1/ai/config", headers=auth_h).json()
        assert body["default_provider"] is None

    def test_cost_cap_round_trip(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put(
            "/v1/ai/config",
            headers=auth_h,
            json={
                "monthly_cost_cap_usd": "12.50",
                "cost_cap_behaviour": "hard_fail",
                "rate_limit_per_hour": 5,
                "fallback_provider": "ollama",
                "fallback_model": "llama3:70b",
                "log_prompts": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["monthly_cost_cap_usd"] == "12.50"
        assert body["cost_cap_behaviour"] == "hard_fail"
        assert body["rate_limit_per_hour"] == 5
        assert body["fallback_provider"] == "ollama"
        assert body["fallback_model"] == "llama3:70b"
        assert body["log_prompts"] is True

    def test_empty_string_clears_cap(self, client: TestClient, auth_h: dict[str, str]) -> None:
        client.put("/v1/ai/config", headers=auth_h, json={"monthly_cost_cap_usd": "5.00"})
        client.put("/v1/ai/config", headers=auth_h, json={"monthly_cost_cap_usd": ""})
        body = client.get("/v1/ai/config", headers=auth_h).json()
        assert body["monthly_cost_cap_usd"] is None

    def test_unknown_key_rejected(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put("/v1/ai/config", headers=auth_h, json={"flarbnox": "yes"})
        assert r.status_code == 422

    def test_invalid_behaviour_rejected(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put("/v1/ai/config", headers=auth_h, json={"cost_cap_behaviour": "explode"})
        assert r.status_code == 422

    def test_requires_admin(self, client: TestClient) -> None:
        r = client.put("/v1/ai/config", json={"default_provider": "anthropic"})
        assert r.status_code == 401


class TestConfigCapability:
    def test_set_then_clear_override(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put(
            "/v1/ai/config/capabilities/categorize",
            headers=auth_h,
            json={"policy": "disabled", "provider": "openai"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["capabilities"]["categorize"]["policy"] == "disabled"
        assert body["capabilities"]["categorize"]["provider"] == "openai"

        r2 = client.put(
            "/v1/ai/config/capabilities/categorize",
            headers=auth_h,
            json={"policy": "__CLEAR__", "provider": "__CLEAR__"},
        )
        body2 = r2.json()
        assert body2["capabilities"]["categorize"] == {
            "policy": None,
            "provider": None,
            "model": None,
            "profile": None,
        }

    def test_unknown_capability_rejected(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put(
            "/v1/ai/config/capabilities/teleport",
            headers=auth_h,
            json={"policy": "disabled"},
        )
        # Path-param validator surfaces a Problem Details.
        assert r.status_code == 422

    def test_unknown_field_rejected(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.put(
            "/v1/ai/config/capabilities/categorize",
            headers=auth_h,
            json={"random_field": "yes"},
        )
        assert r.status_code == 422


class TestStatusP65bExtensions:
    """P6.5.b polish: status includes the new fields + fallback callout."""

    def test_status_includes_p65b_fields_with_defaults(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = client.get("/v1/ai/status", headers=auth_h).json()
        assert body["cost_cap_behaviour"] == "degrade"
        assert body["rate_limit_per_hour"] == 60
        assert body["fallback_provider"] is None
        assert body["month_to_date_spend_usd"] is None

    def test_status_reflects_cap_round_trip(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        client.put(
            "/v1/ai/config",
            headers=auth_h,
            json={
                "monthly_cost_cap_usd": "20.00",
                "cost_cap_behaviour": "hard_fail",
                "fallback_provider": "ollama",
            },
        )
        body = client.get("/v1/ai/status", headers=auth_h).json()
        assert body["monthly_cost_cap_usd"] == "20.00"
        assert body["cost_cap_behaviour"] == "hard_fail"
        assert body["fallback_provider"] == "ollama"
        # MTD is 0 because no calls were made.
        assert body["month_to_date_spend_usd"] == "0"
