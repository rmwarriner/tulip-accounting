"""Unit tests for ``tulip_tui.data.reconciliation_detail`` (P9.6.b)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.reconciliation_detail import (
    ReconciliationDetail,
    auto_match,
    carry_forward,
    complete,
    load_reconciliation_detail,
    manual_match,
    paper_match,
    reject_match,
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


_INBOX_PAYLOAD = {
    "reconciliation": {
        "id": "rec-1",
        "account_id": "acc-1",
        "statement_period_start": "2026-04-01",
        "statement_period_end": "2026-04-30",
        "statement_starting_balance": "1000.00",
        "statement_ending_balance": "1234.56",
        "currency": "USD",
        "status": "open",
        "source_import_batch_id": "batch-9",
        "created_at": "2026-05-01T12:00:00Z",
        "completed_at": None,
    },
    "matches": [
        {
            "id": "match-1",
            "reconciliation_id": "rec-1",
            "statement_line_id": "line-1",
            "ledger_transaction_id": "tx-1",
            "match_amount": "100.00",
            "currency": "USD",
            "confidence": "HIGH",
            "matcher_version": "v1",
            "created_by_user_id": None,
            "created_at": "2026-05-01T12:00:00Z",
        },
        {
            "id": "match-2",
            "reconciliation_id": "rec-1",
            "statement_line_id": "line-2",
            "ledger_transaction_id": "tx-2",
            "match_amount": "50.00",
            "currency": "USD",
            "confidence": None,
            "matcher_version": None,
            "created_by_user_id": "user-1",
            "created_at": "2026-05-01T12:00:00Z",
        },
    ],
    "unmatched_statement_lines": [
        {
            "id": "line-3",
            "line_number": 3,
            "posted_date": "2026-04-15",
            "amount": "-25.50",
            "currency": "USD",
            "description": "AMAZON",
            "counterparty": None,
            "reference": None,
            "fitid": "F3",
        }
    ],
    "unmatched_ledger_transactions": [
        {
            "id": "tx-3",
            "date": "2026-04-15",
            "description": "Amazon order",
            "reference": None,
            "status": "posted",
        }
    ],
}


def test_load_reconciliation_detail_parses_full_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/reconciliations/rec-1"
        return httpx.Response(200, json=_INBOX_PAYLOAD)

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_reconciliation_detail(client, "rec-1")

    assert isinstance(data, ReconciliationDetail)
    assert data.envelope.id == "rec-1"
    assert data.envelope.status == "open"
    assert len(data.matches) == 2
    assert len(data.unmatched_lines) == 1
    assert len(data.unmatched_transactions) == 1


def test_load_reconciliation_detail_classifies_match_kind() -> None:
    with _build_client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=_INBOX_PAYLOAD))
    ) as client:
        data = load_reconciliation_detail(client, "rec-1")

    assert data.matches[0].confidence == "HIGH"
    assert data.matches[0].is_manual is False
    assert data.matches[1].confidence is None
    assert data.matches[1].is_manual is True


def test_load_reconciliation_detail_paper_recon_flag() -> None:
    payload = {**_INBOX_PAYLOAD, "reconciliation": dict(_INBOX_PAYLOAD["reconciliation"])}
    payload["reconciliation"]["source_import_batch_id"] = None
    with _build_client(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))) as client:
        data = load_reconciliation_detail(client, "rec-1")
    assert data.is_paper is True


def test_load_reconciliation_detail_formats_amounts() -> None:
    with _build_client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=_INBOX_PAYLOAD))
    ) as client:
        data = load_reconciliation_detail(client, "rec-1")
    assert data.matches[0].match_amount == "100.00"
    assert data.unmatched_lines[0].amount_display == "-25.50"


def test_load_reconciliation_detail_raises_on_404() -> None:
    from tulip_cli.errors import CliError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "type": "/.well-known/errors/reconciliation.not_found",
                "title": "Not found",
                "status": 404,
                "detail": "x",
                "instance": "",
                "code": "reconciliation.not_found",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        load_reconciliation_detail(client, "missing")


def test_auto_match_calls_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "reconciliation_id": "rec-1",
                "matches_created": 5,
                "candidate_summary": {"high": 3, "medium": 1, "low": 1},
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        result = auto_match(client, "rec-1")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/auto-match"
    assert result["matches_created"] == 5


def test_reject_match_calls_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(204)

    with _build_client(httpx.MockTransport(handler)) as client:
        reject_match(client, "rec-1", "match-1")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/matches/match-1/reject"


def test_manual_match_sends_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "id": "match-new",
                "reconciliation_id": "rec-1",
                "statement_line_id": "line-3",
                "ledger_transaction_id": "tx-3",
                "match_amount": "25.50",
                "currency": "USD",
                "confidence": None,
                "matcher_version": None,
                "created_by_user_id": "user-1",
                "created_at": "2026-05-01T12:00:00Z",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        result = manual_match(
            client,
            "rec-1",
            statement_line_id="line-3",
            ledger_transaction_id="tx-3",
            match_amount="25.50",
            currency="USD",
        )

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/matches"
    assert seen["body"] == {
        "statement_line_id": "line-3",
        "ledger_transaction_id": "tx-3",
        "match_amount": "25.50",
        "currency": "USD",
    }
    assert result["id"] == "match-new"


def test_paper_match_sends_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "id": "match-paper",
                "reconciliation_id": "rec-1",
                "statement_line_id": None,
                "ledger_transaction_id": "tx-3",
                "match_amount": "25.50",
                "currency": "USD",
                "confidence": None,
                "matcher_version": None,
                "created_by_user_id": "user-1",
                "created_at": "2026-05-01T12:00:00Z",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        paper_match(client, "rec-1", ledger_transaction_id="tx-3")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/paper-matches"
    assert seen["body"] == {"ledger_transaction_id": "tx-3"}


def test_carry_forward_sends_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "reconciliation_id": "rec-1",
                "transaction_ids": ["tx-3"],
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        carry_forward(client, "rec-1", transaction_ids=["tx-3"])

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/carry-forward"
    assert seen["body"] == {"transaction_ids": ["tx-3"]}


def test_complete_calls_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "reconciliation_id": "rec-1",
                "status": "complete",
                "completed_at": "2026-05-01T12:00:00Z",
                "affected_transaction_count": 5,
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        result = complete(client, "rec-1")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/reconciliations/rec-1/complete"
    assert result["affected_transaction_count"] == 5
