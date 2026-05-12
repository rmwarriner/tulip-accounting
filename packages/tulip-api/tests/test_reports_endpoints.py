"""Integration tests for the 8 new P7.1 report endpoints.

Each endpoint accepts ``?format=json|html``. These tests confirm:
- JSON shape sane / decodable
- HTML response has ``Content-Type: text/html``
- Auth gate (401 without token)

The data correctness for the underlying queries is covered by the
existing repository / Phase-specific endpoint tests; here we're
checking the report wiring.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


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
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )
    return str(r.json()["access_token"])


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# Each report endpoint, with the minimum query params it requires.
_ENDPOINTS = [
    ("/v1/reports/balance-sheet", {}),
    ("/v1/reports/income-statement", {"start": "2026-01-01", "end": "2026-05-12"}),
    ("/v1/reports/cash-flow", {"start": "2026-01-01", "end": "2026-05-12"}),
    ("/v1/reports/envelope-status", {}),
    ("/v1/reports/sinking-fund-progress", {}),
    ("/v1/reports/reconciliation-summary", {}),
    ("/v1/reports/audit-log", {}),
]


class TestNewReportsJSON:
    """Empty-household JSON returns sane shapes."""

    @pytest.mark.parametrize(("path", "params"), _ENDPOINTS)
    def test_json_default(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        path: str,
        params: dict[str, str],
    ) -> None:
        r = client.get(path, headers=auth_h, params=params)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        # Every report carries household_name + generated_at.
        assert body["household_name"] == "Smith"
        assert "generated_at" in body


class TestNewReportsHTML:
    """``?format=html`` returns a toner-friendly HTML document."""

    @pytest.mark.parametrize(("path", "params"), _ENDPOINTS)
    def test_html_render(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        path: str,
        params: dict[str, str],
    ) -> None:
        merged = dict(params)
        merged["format"] = "html"
        r = client.get(path, headers=auth_h, params=merged)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        assert "Tulip Accounting" in body
        # Toner-friendly base styles surface in every report.
        assert "background: #fff" in body


class TestNewReportsPDF:
    """``?format=pdf`` returns ``application/pdf`` (P7.2)."""

    @pytest.mark.parametrize(("path", "params"), _ENDPOINTS)
    def test_pdf_render(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        path: str,
        params: dict[str, str],
    ) -> None:
        merged = dict(params)
        merged["format"] = "pdf"
        r = client.get(path, headers=auth_h, params=merged)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        # PDF magic bytes — gives confidence weasyprint actually produced a file
        # rather than the JSON / HTML branches accidentally falling through.
        assert r.content.startswith(b"%PDF-")
        # Content-Disposition header surfaces a sensible filename.
        assert "filename=" in r.headers.get("content-disposition", "")


class TestAuth:
    """All new report endpoints require auth."""

    @pytest.mark.parametrize(("path", "params"), _ENDPOINTS)
    def test_no_token_returns_unauthorized(
        self,
        client: TestClient,
        path: str,
        params: dict[str, str],
    ) -> None:
        r = client.get(path, params=params)
        assert_problem(r, code="auth.unauthorized", status=401)


class TestCustomQuery:
    """``/custom-query`` requires the ``sql`` query parameter + validates it."""

    def test_select_against_ai_view_succeeds(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        """A valid SELECT against the allowlisted ai_view_transactions view runs."""
        r = client.get(
            "/v1/reports/custom-query",
            headers=auth_h,
            params={"sql": "SELECT amount, currency FROM ai_view_transactions LIMIT 5"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "sql" in body
        assert "rows" in body
        assert body["truncated"] is False

    def test_unsafe_sql_returns_400_problem(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        """Querying a non-allowlisted table surfaces a typed Problem Details."""
        r = client.get(
            "/v1/reports/custom-query",
            headers=auth_h,
            params={"sql": "SELECT * FROM users"},
        )
        assert r.status_code == 400
        body = r.json()
        assert body["code"] == "report.unsafe_query"

    def test_missing_sql_param_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.get("/v1/reports/custom-query", headers=auth_h)
        assert r.status_code == 422

    def test_no_token_returns_unauthorized(self, client: TestClient) -> None:
        r = client.get("/v1/reports/custom-query", params={"sql": "SELECT 1"})
        assert_problem(r, code="auth.unauthorized", status=401)


# Date filter passes through correctly on income-statement / cash-flow.
class TestDateRangeReports:
    def test_income_statement_returns_period_meta(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.get(
            "/v1/reports/income-statement",
            headers=auth_h,
            params={"start": "2026-01-01", "end": "2026-05-12"},
        )
        body = r.json()
        assert body["current_period"]["start"] == "2026-01-01"
        assert body["current_period"]["end"] == "2026-05-12"
        assert body["prior_period"] is None

    def test_income_statement_with_comparison_period(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.get(
            "/v1/reports/income-statement",
            headers=auth_h,
            params={
                "start": "2026-04-01",
                "end": "2026-04-30",
                "prior_start": "2026-03-01",
                "prior_end": "2026-03-31",
            },
        )
        body = r.json()
        assert body["prior_period"]["start"] == "2026-03-01"
        assert body["prior_period"]["end"] == "2026-03-31"


# Use the date import so linters don't flag it as unused after parametrization.
_ = date
