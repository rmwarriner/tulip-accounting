"""Tests for the RFC 9457 error renderer + exit-code map.

ARCHITECTURE.md §7.8.5 specifies CLI exit codes:
    0 success, 1 user error, 2 auth, 3 server, 4 network, 5 configuration.
"""

from __future__ import annotations

import json

import httpx

from tulip_cli.errors import (
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_NETWORK,
    EXIT_SERVER,
    EXIT_USER,
    CliError,
    exit_code_for_problem,
    exit_code_for_status,
    parse_problem_response,
)


def _problem(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "/.well-known/errors/transaction.unbalanced",
        "title": "Transaction does not balance",
        "status": 400,
        "detail": "Sum of postings is 0.50, expected 0.00.",
        "instance": "/v1/transactions",
        "code": "transaction.unbalanced",
    }
    base.update(overrides)
    return base


def test_exit_code_map_per_status() -> None:
    assert exit_code_for_status(400) == EXIT_USER
    assert exit_code_for_status(401) == EXIT_AUTH
    assert exit_code_for_status(403) == EXIT_AUTH
    assert exit_code_for_status(404) == EXIT_USER
    assert exit_code_for_status(409) == EXIT_USER
    assert exit_code_for_status(422) == EXIT_USER
    assert exit_code_for_status(500) == EXIT_SERVER
    assert exit_code_for_status(502) == EXIT_SERVER
    assert exit_code_for_status(503) == EXIT_SERVER


def test_exit_code_for_problem_uses_status() -> None:
    problem = _problem(status=503, code="upstream.unavailable")
    assert exit_code_for_problem(problem) == EXIT_SERVER


def test_render_problem_emits_title_and_detail(capsys: object) -> None:
    err = CliError(problem=_problem(), as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Transaction does not balance" in captured.err
    assert "Sum of postings" in captured.err


def test_render_problem_json_mode_emits_raw_body(capsys: object) -> None:
    body = _problem()
    err = CliError(problem=body, as_json=True)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out)
    assert parsed == body


def test_parse_problem_response_with_problem_json() -> None:
    body = _problem()
    response = httpx.Response(
        400,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/problem+json"},
    )
    parsed = parse_problem_response(response)
    assert parsed == body


def test_parse_problem_response_html_body_treated_as_wrong_server() -> None:
    """HTML 4xx/5xx → not a Tulip API; map to a config-level problem (exit 5)."""
    request = httpx.Request("GET", "https://example.com/health")
    response = httpx.Response(
        404,
        content=b"<html>not found</html>",
        headers={"content-type": "text/html"},
        request=request,
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 404
    assert parsed["code"] == "config.not_a_tulip_api"
    assert "Tulip" in parsed["title"]
    assert "https://example.com" in parsed["detail"]


def test_html_response_yields_exit_config_when_wrapped_in_clierror() -> None:
    request = httpx.Request("GET", "https://example.com/health")
    response = httpx.Response(
        404,
        content=b"<html>not found</html>",
        headers={"content-type": "text/html"},
        request=request,
    )
    err = CliError.from_response(response, as_json=False)
    assert err.exit_code == EXIT_CONFIG


def test_parse_problem_response_json_but_not_problem_body_keeps_unexpected() -> None:
    """A real Tulip-ish API returning plain JSON for an error is still a server bug, not config."""
    request = httpx.Request("GET", "https://api.example.com/v1/accounts")
    response = httpx.Response(
        500,
        content=b'{"oops": true}',
        headers={"content-type": "application/json"},
        request=request,
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 500
    assert parsed["code"] == "server.unexpected_response"


def test_parse_problem_response_falls_back_for_request_less_response() -> None:
    """Synthesized responses with no request attached still produce a problem dict."""
    response = httpx.Response(
        500,
        content=b"<html>boom</html>",
        headers={"content-type": "text/html"},
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 500
    assert parsed["code"] == "config.not_a_tulip_api"
    assert "title" in parsed
    assert "detail" in parsed


def test_exit_code_for_problem_routes_config_codes_to_exit_5() -> None:
    problem = {"code": "config.not_a_tulip_api", "status": 404}
    assert exit_code_for_problem(problem) == EXIT_CONFIG


def test_exit_code_for_problem_routes_network_codes_to_exit_4() -> None:
    problem = {"code": "network.unreachable", "status": 0}
    assert exit_code_for_problem(problem) == EXIT_NETWORK


def test_network_error_maps_to_exit_4() -> None:
    err = CliError.from_network_error(httpx.ConnectError("nope"), as_json=False)
    assert err.exit_code == EXIT_NETWORK


def test_render_problem_includes_request_id_when_present(capsys: object) -> None:
    body = _problem(request_id="abc-123")
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "abc-123" in captured.err
