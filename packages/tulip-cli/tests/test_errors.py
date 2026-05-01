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


def test_parse_problem_response_html_4xx_treated_as_wrong_server() -> None:
    """4xx + non-JSON body → wrong host. Misconfigured DNS / wrong port returns 404 HTML."""
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
    assert str(request.url) in parsed["detail"]


def test_parse_problem_response_text_5xx_is_server_bug_not_wrong_server() -> None:
    """5xx + non-JSON body → real API crashed, not wrong-host.

    A 500 with text/plain is what Starlette emits when an unhandled
    exception escapes before the Problem Details handler can wrap it.
    Telling the user "you've got the wrong URL" in that case is wrong
    and unhelpful — they want to know the server crashed.
    """
    request = httpx.Request("POST", "http://127.0.0.1:8000/v1/auth/register")
    response = httpx.Response(
        500,
        content=b"Internal Server Error",
        headers={"content-type": "text/plain; charset=utf-8"},
        request=request,
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 500
    assert parsed["code"] == "server.unexpected_response"
    err = CliError(problem=parsed, as_json=False)
    assert err.exit_code == EXIT_SERVER


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
        404,
        content=b"<html>not found</html>",
        headers={"content-type": "text/html"},
    )
    parsed = parse_problem_response(response)
    assert parsed["status"] == 404
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


def test_render_problem_surfaces_pydantic_validation_errors(capsys: object) -> None:
    """validation.failed bodies carry a Pydantic-shaped ``errors`` extension. Render it."""
    body = {
        "type": "/.well-known/errors/validation.failed",
        "title": "Request validation failed",
        "status": 422,
        "detail": "One or more fields in the request body or query parameters are invalid.",
        "instance": "/v1/auth/register",
        "code": "validation.failed",
        "errors": [
            {
                "type": "string_too_short",
                "loc": ["body", "password"],
                "msg": "String should have at least 12 characters",
                "input": "shorty",
            },
            {
                "type": "value_error",
                "loc": ["body", "email"],
                "msg": "value is not a valid email address",
                "input": "bad-email",
            },
        ],
    }
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    # The leading ``body``/``query``/``path`` segment is Pydantic's loc
    # convention for "this came from the request body" and is jargon to
    # the user. The renderer strips it so messages read naturally.
    assert "password" in captured.err
    assert "body.password" not in captured.err
    assert "at least 12 characters" in captured.err
    assert "email" in captured.err
    assert "body.email" not in captured.err
    assert "valid email address" in captured.err


def test_render_problem_keeps_inner_loc_segments(capsys: object) -> None:
    """Stripping is one segment deep; nested locations stay legible."""
    body = {
        "title": "Request validation failed",
        "status": 422,
        "detail": "...",
        "code": "validation.failed",
        "errors": [
            {
                "loc": ["body", "postings", 0, "account_id"],
                "msg": "field required",
            }
        ],
    }
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "postings.0.account_id" in captured.err
    # No leading "body." after the strip.
    assert "body.postings" not in captured.err


def test_render_problem_ignores_non_list_errors_extension(capsys: object) -> None:
    """An ``errors`` extension that isn't pydantic-shaped is silently skipped."""
    body = {
        "title": "Something",
        "status": 422,
        "detail": "...",
        "code": "validation.failed",
        "errors": "not a list",
    }
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "not a list" not in captured.err


def test_render_problem_json_mode_does_not_split_errors_extension(capsys: object) -> None:
    """--json mode passes the body through verbatim, errors extension included."""
    body = {
        "title": "Request validation failed",
        "status": 422,
        "code": "validation.failed",
        "errors": [{"loc": ["body", "x"], "msg": "nope"}],
    }
    err = CliError(problem=body, as_json=True)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out)
    assert parsed == body


def test_render_problem_includes_request_id_when_present(capsys: object) -> None:
    body = _problem(request_id="abc-123")
    err = CliError(problem=body, as_json=False)
    err.render()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "abc-123" in captured.err
