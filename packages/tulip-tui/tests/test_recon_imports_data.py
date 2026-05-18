"""Unit tests for ``tulip_tui.data.reconciliations`` + ``data.imports``.

Both adapters wrap a single read endpoint with no joining or
mutation. Empty-state, normal-state, and API-error paths are covered.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.imports import (
    ImportBatchSummary,
    ImportsData,
    load_import_batches,
)
from tulip_tui.data.reconciliations import (
    ReconciliationsData,
    ReconciliationSummary,
    load_reconciliations,
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


# ---- reconciliations ------------------------------------------------


_RECON_PAYLOAD = [
    {
        "id": "rec-1",
        "account_id": "acc-1",
        "statement_period_start": "2026-04-01",
        "statement_period_end": "2026-04-30",
        "statement_starting_balance": "1000.00",
        "statement_ending_balance": "1234.56",
        "currency": "USD",
        "status": "complete",
        "source_import_batch_id": None,
        "created_at": "2026-05-01T12:00:00Z",
        "completed_at": "2026-05-02T09:30:00Z",
    },
    {
        "id": "rec-2",
        "account_id": "acc-2",
        "statement_period_start": "2026-05-01",
        "statement_period_end": "2026-05-31",
        "statement_starting_balance": "500.00",
        "statement_ending_balance": "725.10",
        "currency": "USD",
        "status": "open",
        "source_import_batch_id": "batch-9",
        "created_at": "2026-05-15T08:00:00Z",
        "completed_at": None,
    },
]


def test_load_reconciliations_returns_summary_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/reconciliations"
        return httpx.Response(200, json=_RECON_PAYLOAD)

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_reconciliations(client)

    assert isinstance(data, ReconciliationsData)
    assert len(data.reconciliations) == 2
    first = data.reconciliations[0]
    assert isinstance(first, ReconciliationSummary)
    assert first.id == "rec-1"
    assert first.status == "complete"
    assert first.currency == "USD"
    assert first.statement_period_start == "2026-04-01"
    assert first.statement_period_end == "2026-04-30"
    assert first.statement_ending_balance == "1234.56"


def test_load_reconciliations_handles_empty() -> None:
    with _build_client(httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))) as client:
        data = load_reconciliations(client)
    assert data.reconciliations == ()


def test_load_reconciliations_raises_on_error() -> None:
    from tulip_cli.errors import CliError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "type": "/.well-known/errors/internal",
                "title": "Internal server error",
                "status": 500,
                "detail": "boom",
                "instance": "",
                "code": "internal",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        load_reconciliations(client)


# ---- import batches -------------------------------------------------


_IMPORTS_PAYLOAD = [
    {
        "id": "batch-1",
        "account_id": "acc-1",
        "source_format": "ofx",
        "source_filename": "april.qfx",
        "status": "applied",
        "imported_count": 42,
        "skipped_count": 0,
        "error_count": 0,
        "created_at": "2026-05-01T12:00:00Z",
        "applied_at": "2026-05-01T12:05:00Z",
        "reverted_at": None,
    },
    {
        "id": "batch-2",
        "account_id": "acc-2",
        "source_format": "csv",
        "source_filename": "visa.csv",
        "status": "parsed",
        "imported_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "created_at": "2026-05-10T09:00:00Z",
        "applied_at": None,
        "reverted_at": None,
    },
]


def test_load_import_batches_returns_summary_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/imports"
        return httpx.Response(200, json=_IMPORTS_PAYLOAD)

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_import_batches(client)

    assert isinstance(data, ImportsData)
    assert len(data.batches) == 2
    first = data.batches[0]
    assert isinstance(first, ImportBatchSummary)
    assert first.id == "batch-1"
    assert first.source_format == "ofx"
    assert first.source_filename == "april.qfx"
    assert first.status == "applied"
    assert first.imported_count == 42


def test_load_import_batches_handles_empty() -> None:
    with _build_client(httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))) as client:
        data = load_import_batches(client)
    assert data.batches == ()


def test_load_import_batches_raises_on_error() -> None:
    from tulip_cli.errors import CliError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "type": "/.well-known/errors/internal",
                "title": "Internal server error",
                "status": 500,
                "detail": "boom",
                "instance": "",
                "code": "internal",
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        load_import_batches(client)
