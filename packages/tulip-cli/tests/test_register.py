"""End-to-end tests for ``tulip register``.

Each test spawns a real uvicorn (via the ``live_api`` fixture) and runs
the ``tulip`` console script as a subprocess. That's the only way to
exercise stdin prompting, stdout/stderr separation, and the real exit
codes the user actually sees.

Note: the API's per-household email uniqueness means ``register`` cannot
trigger ``auth.duplicate_email`` in practice — each call mints a new
household with a fresh UUID, so the ``(household_id, email)`` pair is
unique by construction. Cross-household re-use of an email is by design
(see ``models/user.py`` and the comment in ``routers/auth.login``).
That's why the duplicate-email assertion below verifies *acceptance*,
not rejection.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run_cli(
    *args: str, api_url: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=15,
    )


@pytest.mark.integration
def test_register_happy_path(live_api: str) -> None:
    result = _run_cli(
        "register",
        "--email",
        "alice@example.com",
        "--display-name",
        "Alice",
        "--household",
        "The Smiths",
        "--password-stdin",
        api_url=live_api,
        stdin="this-is-a-strong-password\n",
    )
    assert result.returncode == 0, result.stderr
    assert "alice@example.com" in result.stdout
    assert "admin" in result.stdout.lower()


@pytest.mark.integration
def test_register_short_password_via_stdin_fails_fast(live_api: str) -> None:
    """``--password-stdin`` validates client-side and never hits the API."""
    result = _run_cli(
        "register",
        "--email",
        "shorty@example.com",
        "--display-name",
        "Shorty",
        "--household",
        "Shorty's House",
        "--password-stdin",
        api_url=live_api,
        stdin="short\n",
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "12" in result.stderr  # the min-length constraint named in the message
    assert "password" in result.stderr.lower()


@pytest.mark.integration
def test_register_json_emits_response_body_on_success(live_api: str) -> None:
    result = _run_cli(
        "--json",
        "register",
        "--email",
        "carol@example.com",
        "--display-name",
        "Carol",
        "--household",
        "Carol's House",
        "--password-stdin",
        api_url=live_api,
        stdin="this-is-a-strong-password\n",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["role"] == "admin"
    assert "user_id" in payload
    assert "household_id" in payload


@pytest.mark.integration
def test_register_json_emits_problem_body_on_failure(live_api: str) -> None:
    result = _run_cli(
        "--json",
        "register",
        "--email",
        "bad-email-no-at-sign",
        "--display-name",
        "Whoever",
        "--household",
        "Whoever's House",
        "--password-stdin",
        api_url=live_api,
        stdin="this-is-a-strong-password\n",
    )
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["status"] == 422
    assert body["code"] == "validation.failed"


@pytest.mark.integration
def test_register_same_email_different_households_both_succeed(live_api: str) -> None:
    """Documented contract: emails are unique per-household, not globally."""
    common = {"api_url": live_api, "stdin": "this-is-a-strong-password\n"}
    base_args = (
        "register",
        "--email",
        "shared@example.com",
        "--display-name",
        "Whoever",
        "--password-stdin",
    )
    first = _run_cli(*base_args, "--household", "House One", **common)
    second = _run_cli(*base_args, "--household", "House Two", **common)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
