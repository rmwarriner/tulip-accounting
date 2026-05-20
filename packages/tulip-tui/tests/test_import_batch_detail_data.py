"""Unit tests for ``tulip_tui.data.import_batch_detail`` (P9.6.a).

Covers the per-batch detail loader plus the three action wrappers
(``patch_line_excluded`` / ``promote_line`` / ``apply_batch``). All
tests use ``httpx.MockTransport`` so the live API is never touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.import_batch_detail import (
    ImportBatchDetail,
    StatementLineSummary,
    apply_batch,
    load_import_batch_detail,
    patch_line_excluded,
    promote_line,
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


_BATCH_PAYLOAD: dict[str, object] = {
    "id": "batch-1",
    "account_id": "acc-1",
    "source_format": "ofx",
    "source_filename": "april.qfx",
    "status": "parsed",
    "imported_count": 3,
    "skipped_count": 0,
    "error_count": 0,
    "created_at": "2026-05-01T12:00:00Z",
    "applied_at": None,
    "reverted_at": None,
    "lines": [
        {
            "id": "line-1",
            "line_number": 1,
            "posted_date": "2026-05-01",
            "amount": "-42.17",
            "currency": "USD",
            "description": "AMAZON",
            "counterparty": None,
            "reference": None,
            "fitid": "F1",
            "is_excluded": False,
            "reconciliation_match_id": None,
            "promoted_transaction_id": None,
        },
        {
            "id": "line-2",
            "line_number": 2,
            "posted_date": "2026-05-02",
            "amount": "-12.50",
            "currency": "USD",
            "description": "LUNCH",
            "counterparty": None,
            "reference": None,
            "fitid": "F2",
            "is_excluded": True,
            "reconciliation_match_id": None,
            "promoted_transaction_id": None,
        },
        {
            "id": "line-3",
            "line_number": 3,
            "posted_date": "2026-05-03",
            "amount": "100.00",
            "currency": "USD",
            "description": "PAYCHECK",
            "counterparty": None,
            "reference": None,
            "fitid": "F3",
            "is_excluded": False,
            "reconciliation_match_id": None,
            "promoted_transaction_id": "tx-99",
        },
    ],
}


def test_load_import_batch_detail_round_trips() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/imports/batch-1"
        return httpx.Response(200, json=_BATCH_PAYLOAD)

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_import_batch_detail(client, "batch-1")

    assert isinstance(data, ImportBatchDetail)
    assert data.id == "batch-1"
    assert data.source_format == "ofx"
    assert len(data.lines) == 3
    assert isinstance(data.lines[0], StatementLineSummary)


def test_load_import_batch_detail_classifies_statuses() -> None:
    with _build_client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=_BATCH_PAYLOAD))
    ) as client:
        data = load_import_batch_detail(client, "batch-1")

    by_id = {line.id: line for line in data.lines}
    assert by_id["line-1"].status == "pending"
    assert by_id["line-2"].status == "excluded"
    assert by_id["line-3"].status == "promoted"

    assert data.pending_count == 1
    assert data.excluded_count == 1
    assert data.promoted_count == 1


def test_load_import_batch_detail_handles_empty_lines() -> None:
    payload = dict(_BATCH_PAYLOAD)
    payload["lines"] = []
    with _build_client(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))) as client:
        data = load_import_batch_detail(client, "batch-1")
    assert data.lines == ()
    assert data.pending_count == 0


def test_load_import_batch_detail_amount_display_formatted() -> None:
    with _build_client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=_BATCH_PAYLOAD))
    ) as client:
        data = load_import_batch_detail(client, "batch-1")
    assert data.lines[0].amount_display == "-42.17"
    assert data.lines[2].amount_display == "100.00"


def test_load_import_batch_detail_raises_on_404() -> None:
    from tulip_cli.errors import CliError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "type": "/.well-known/errors/import_batch.not_found",
                "title": "Not found",
                "status": 404,
                "detail": "x",
                "instance": "",
                "code": "import_batch.not_found",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        load_import_batch_detail(client, "missing")


def test_patch_line_excluded_sends_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        import json

        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "line-1",
                "line_number": 1,
                "posted_date": "2026-05-01",
                "amount": "-42.17",
                "currency": "USD",
                "description": "AMAZON",
                "counterparty": None,
                "reference": None,
                "fitid": None,
                "is_excluded": True,
                "reconciliation_match_id": None,
                "promoted_transaction_id": None,
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        patch_line_excluded(client, "batch-1", "line-1", is_excluded=True)

    assert seen["method"] == "PATCH"
    assert seen["path"] == "/v1/imports/batch-1/lines/line-1"
    assert seen["body"] == {"is_excluded": True}


def test_promote_line_calls_promote_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            201,
            json={
                "statement_line_id": "line-1",
                "transaction_id": "tx-new",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        promote_line(client, "batch-1", "line-1")

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/imports/batch-1/lines/line-1/promote"


def test_apply_batch_passes_flags_as_query_params() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "batch_id": "batch-1",
                "status": "applied",
                "created_count": 3,
                "skipped_count": 0,
                "transaction_ids": ["t1", "t2", "t3"],
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        result = apply_batch(
            client,
            "batch-1",
            as_posted=True,
            no_categorize=False,
            treat_cleared_as_pending=True,
        )

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/imports/batch-1/apply"
    # ``no_categorize=False`` must NOT appear in the query string.
    assert seen["query"] == {"as_posted": "true", "treat_cleared_as_pending": "true"}
    assert result["created_count"] == 3


def test_apply_batch_with_no_flags_omits_params() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "batch_id": "batch-1",
                "status": "applied",
                "created_count": 0,
                "skipped_count": 0,
                "transaction_ids": [],
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        apply_batch(client, "batch-1")

    assert seen["query"] == {}
