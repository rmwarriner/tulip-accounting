"""Unit tests for ``tulip_tui.data.reports``.

The reports browser doesn't transform the API response heavily — it
just fetches the raw JSON body and hands it to a renderer. The data
layer's job is to:

* Carry the catalogue of browsable reports (name, endpoint, default
  params) — single source of truth that the screen iterates over.
* Wrap the actual HTTP fetch so the screen layer can be tested
  against in-memory fixtures.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.reports import (
    REPORT_CATALOGUE,
    ReportPayload,
    load_report,
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


def test_report_catalogue_lists_browsable_reports() -> None:
    """All eight v1 browse-able reports are catalogued in display order."""
    keys = [spec.key for spec in REPORT_CATALOGUE]
    assert keys == [
        "trial-balance",
        "balance-sheet",
        "income-statement",
        "cash-flow",
        "envelope-status",
        "sinking-fund-progress",
        "reconciliation-summary",
        "audit-log",
    ]
    for spec in REPORT_CATALOGUE:
        assert spec.title  # non-empty label
        assert spec.endpoint.startswith("/v1/reports/")


def test_load_report_round_trips_json_payload() -> None:
    """``load_report`` fetches the endpoint and wraps the JSON body."""
    spec = next(s for s in REPORT_CATALOGUE if s.key == "trial-balance")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == spec.endpoint
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "as_of": "2026-05-17",
                "rows": [
                    {
                        "account_id": "a",
                        "code": "assets:checking",
                        "name": "Checking",
                        "type": "asset",
                        "currency": "USD",
                        "balance": "100.00",
                        "has_pending": False,
                    }
                ],
                "totals_by_currency": [{"currency": "USD", "debits": "100.00", "credits": "0.00"}],
                "pending_included": False,
                "pending_count": 0,
            },
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        payload = load_report(client, spec)

    # JSON format is forced when the spec's default doesn't override it.
    assert captured.get("format") == "json"
    assert isinstance(payload, ReportPayload)
    assert payload.spec is spec
    assert isinstance(payload.body, dict)
    assert payload.body["as_of"] == "2026-05-17"


def test_load_report_passes_default_params_through() -> None:
    """Reports with default params (e.g., income-statement) send them through."""
    spec = next(s for s in REPORT_CATALOGUE if s.key == "income-statement")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"start": "2026-05-01", "end": "2026-05-31", "revenue": [], "expenses": []},
        )

    with _build_client(httpx.MockTransport(handler)) as client:
        load_report(client, spec)

    assert "start" in captured
    assert "end" in captured


def test_load_report_propagates_api_error() -> None:
    """An API error bubbles out as ``CliError`` for the screen to render."""
    from tulip_cli.errors import CliError

    spec = REPORT_CATALOGUE[0]

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
        load_report(client, spec)


def test_report_spec_is_immutable() -> None:
    """``ReportSpec`` is a frozen dataclass — catalogue entries can't be mutated."""
    spec = REPORT_CATALOGUE[0]
    with pytest.raises(AttributeError):
        spec.title = "tampered"  # type: ignore[misc]
