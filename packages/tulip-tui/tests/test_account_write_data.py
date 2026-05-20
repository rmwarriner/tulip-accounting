"""Unit tests for ``tulip_tui.data.account_write`` (#431)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.account_write import (
    AccountDraft,
    create_account,
    list_parent_candidates,
    update_account,
)


class _FakeTokenStore:
    def load(self, _api_url: str) -> object:
        return SimpleNamespace(
            email="t@example.invalid",
            access_token="fake",
            refresh_token="fake",
            access_expires_at=2**31 - 1,
        )

    def save(self, _api_url: str, _tokens: object) -> None: ...
    def clear(self, _api_url: str) -> None: ...


def _client(handler: httpx.MockTransport) -> TulipClient:
    return TulipClient(
        Config(api_url="https://example.invalid"),
        token_store=_FakeTokenStore(),  # type: ignore[arg-type]
        transport=handler,
    )


def test_create_account_omits_optional_fields() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "id": "acc-1",
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "1110",
                "subtype": None,
                "visibility": "shared",
                "is_active": True,
                "parent_account_id": None,
            },
        )

    draft = AccountDraft(
        name="Checking",
        type="asset",
        currency="USD",
        code="1110",
        subtype=None,
        visibility="shared",
        parent_account_id=None,
    )
    with _client(httpx.MockTransport(handler)) as client:
        result = create_account(client, draft)

    # subtype and parent are omitted when None.
    assert "subtype" not in seen["body"]
    assert "parent_account_id" not in seen["body"]
    assert seen["body"]["name"] == "Checking"
    assert seen["body"]["code"] == "1110"
    assert result["id"] == "acc-1"


def test_create_account_includes_parent_when_set() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={"id": "acc-2", "name": "Sub", "type": "asset", "currency": "USD"},
        )

    draft = AccountDraft(
        name="Sub",
        type="asset",
        currency="USD",
        code=None,
        subtype="bank",
        visibility="shared",
        parent_account_id="parent-uuid",
    )
    with _client(httpx.MockTransport(handler)) as client:
        create_account(client, draft)

    assert seen["body"]["subtype"] == "bank"
    assert seen["body"]["parent_account_id"] == "parent-uuid"
    assert "code" not in seen["body"]


def test_update_account_calls_patch() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"id": "acc-1", "name": "Updated", "type": "asset", "currency": "USD"},
        )

    with _client(httpx.MockTransport(handler)) as client:
        update_account(client, "acc-1", {"name": "Updated"})

    assert seen["method"] == "PATCH"
    assert seen["path"] == "/v1/accounts/acc-1"
    assert seen["body"] == {"name": "Updated"}


def test_list_parent_candidates_filters_by_type_and_currency() -> None:
    accounts = [
        {"id": "a1", "name": "Checking", "type": "asset", "currency": "USD", "code": "1110"},
        {"id": "a2", "name": "Savings", "type": "asset", "currency": "USD", "code": "1120"},
        {"id": "a3", "name": "Euros", "type": "asset", "currency": "EUR", "code": "1130"},
        {"id": "a4", "name": "Visa", "type": "liability", "currency": "USD", "code": "2110"},
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=accounts)

    with _client(httpx.MockTransport(handler)) as client:
        out = list_parent_candidates(client, account_type="asset", currency="USD")

    ids = [c.id for c in out]
    assert ids == ["a1", "a2"]  # EUR + liability filtered out


def test_list_parent_candidates_empty_when_no_match() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(handler)) as client:
        out = list_parent_candidates(client, account_type="asset", currency="USD")
    assert out == ()


def test_create_account_raises_on_4xx() -> None:
    from tulip_cli.errors import CliError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "type": "/.well-known/errors/account.parent_type_mismatch",
                "title": "Parent account has a different type",
                "status": 400,
                "detail": "parent.type=liability; child.type=asset",
                "instance": "",
                "code": "account.parent_type_mismatch",
            },
        )

    draft = AccountDraft(
        name="x",
        type="asset",
        currency="USD",
        code=None,
        subtype=None,
        visibility="shared",
        parent_account_id="bad-parent",
    )
    with _client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        create_account(client, draft)
