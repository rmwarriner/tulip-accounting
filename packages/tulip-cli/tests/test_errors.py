"""Tests for the RFC 9457 error renderer + exit-code map.

ARCHITECTURE.md §7.8.5 specifies CLI exit codes:
    0 success, 1 user error, 2 auth, 3 server, 4 network, 5 configuration.
"""

from __future__ import annotations

import json

import httpx

from tulip_cli.errors import (
    EXIT_AUTH,
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


def test_parse_problem_response_falls_back_for_non_problem_body() -> None:
    response = httpx.Response(
        500,
        content=b"<html>boom</html>",
        headers={"content-type": "text/html"},
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 500
    assert parsed["code"] == "server.unexpected_response"
    assert "title" in parsed
    assert "detail" in parsed


def test_network_error_maps_to_exit_4() -> None:
    err = CliError.from_network_error(httpx.ConnectError("nope"), as_json=False)
    assert err.exit_code == EXIT_NETWORK


def test_render_problem_includes_request_id_when_present(capsys: object) -> None:
    body = _problem(request_id="abc-123")
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "abc-123" in captured.err
