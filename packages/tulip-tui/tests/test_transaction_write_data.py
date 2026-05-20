"""Unit tests for ``tulip_tui.data.transaction_write`` (P9.6.c)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.transaction_write import (
    ParsedPosting,
    TransactionDraft,
    create_transaction,
    delete_transaction,
    parse_posting_line,
    parse_postings_block,
    update_transaction,
    void_transaction,
)


class _FakeTokenStore:
    def load(self, _api_url: str) -> object:
        return SimpleNamespace(
            email="t@example.invalid",
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            access_expires_at=2**31 - 1,
        )

    def save(self, _api_url: str, _tokens: object) -> None: ...
    def clear(self, _api_url: str) -> None: ...


def _build_client(handler: httpx.MockTransport) -> TulipClient:
    return TulipClient(
        Config(api_url="https://example.invalid"),
        token_store=_FakeTokenStore(),  # type: ignore[arg-type]
        transport=handler,
    )


# ---- parser ---------------------------------------------------------------


def test_parse_posting_basic() -> None:
    p = parse_posting_line("1110=-12.50")
    assert p == ParsedPosting(account="1110", amount=Decimal("-12.50"), currency=None)


def test_parse_posting_with_currency() -> None:
    p = parse_posting_line("1110=42@USD")
    assert p == ParsedPosting(account="1110", amount=Decimal("42"), currency="USD")


def test_parse_posting_strips_whitespace() -> None:
    p = parse_posting_line("  Food = 12.34  @ usd  ")
    assert p == ParsedPosting(account="Food", amount=Decimal("12.34"), currency="USD")


def test_parse_posting_raises_on_bad_shape() -> None:
    with pytest.raises(ValueError):
        parse_posting_line("not a posting")


def test_parse_posting_raises_on_bad_amount() -> None:
    with pytest.raises(ValueError):
        parse_posting_line("1110=oops")


def test_parse_postings_block_skips_comments_and_blank_lines() -> None:
    text = "\n".join(
        [
            "# comment",
            "",
            "1110=-12.50",
            "5100=12.50",
            "  ",
            "# another",
        ]
    )
    out = parse_postings_block(text)
    assert len(out) == 2


def test_parse_postings_block_requires_two_postings() -> None:
    with pytest.raises(ValueError):
        parse_postings_block("1110=-12.50")


def test_parse_postings_block_reports_line_number() -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse_postings_block("1110=-12.50\nbogus\n5100=12.50")


# ---- API wrappers ---------------------------------------------------------


def test_create_transaction_resolves_code_and_posts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {"id": "uuid-checking", "code": "1110", "name": "Checking"},
                    {"id": "uuid-food", "code": "5100", "name": "Food"},
                ],
            )
        if request.method == "POST" and request.url.path == "/v1/transactions":
            return httpx.Response(
                201,
                json={
                    "id": "tx-new",
                    "date": "2026-05-20",
                    "description": "Lunch",
                    "postings": [],
                },
            )
        return httpx.Response(500)

    draft = TransactionDraft(
        date="2026-05-20",
        description="Lunch",
        reference=None,
        postings=(
            ParsedPosting(account="1110", amount=Decimal("-12.50"), currency=None),
            ParsedPosting(account="5100", amount=Decimal("12.50"), currency=None),
        ),
    )
    with _build_client(httpx.MockTransport(handler)) as client:
        result = create_transaction(client, draft)

    # Body should reference resolved UUIDs.
    post_req = next(r for r in seen if r.method == "POST" and r.url.path == "/v1/transactions")
    import json as _json

    body = _json.loads(post_req.content.decode())
    assert body["postings"][0]["account_id"] == "uuid-checking"
    assert body["postings"][1]["account_id"] == "uuid-food"
    # No currency key when None was given (API infers from account).
    assert "currency" not in body["postings"][0]
    assert result["id"] == "tx-new"


def test_create_transaction_raises_on_unknown_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    draft = TransactionDraft(
        date="2026-05-20",
        description="x",
        reference=None,
        postings=(
            ParsedPosting(account="missing", amount=Decimal("1"), currency=None),
            ParsedPosting(account="other", amount=Decimal("-1"), currency=None),
        ),
    )
    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        create_transaction(client, draft)


def test_void_transaction_sends_reason() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"source_id": "tx-1", "reversal_id": "tx-2"},
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        result = void_transaction(client, "tx-1", reason="wrong amount")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/transactions/tx-1/void"
    assert seen["body"] == {"reason": "wrong amount"}
    assert result["reversal_id"] == "tx-2"


def test_delete_transaction_calls_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(204)

    with _build_client(httpx.MockTransport(handler)) as client:
        delete_transaction(client, "tx-1")

    assert seen["method"] == "DELETE"
    assert seen["path"] == "/v1/transactions/tx-1"


def test_update_transaction_patches() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(200, json={"id": "tx-1", "description": "updated"})

    with _build_client(httpx.MockTransport(handler)) as client:
        result = update_transaction(client, "tx-1", {"description": "updated"})

    assert seen["method"] == "PATCH"
    assert seen["path"] == "/v1/transactions/tx-1"
    assert seen["body"] == {"description": "updated"}
    assert result["description"] == "updated"
