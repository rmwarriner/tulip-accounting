"""Integration tests for the picker wired into the three target commands (#273).

These cover the seam between the command's "argument missing" path and
the shared :mod:`tulip_cli._picker` helper:

- happy path: TTY + non-empty list + numeric pick → resolved id flows to
  the HTTP call.
- cancel path: TTY + ``c`` → no follow-up HTTP call, exit code 2.
- non-TTY path: piped stdin → legacy usage-error stderr + exit code 2.

We drive the resolver functions directly with an ``httpx.MockTransport``
to avoid the cost of a uvicorn spawn and to keep assertions deterministic
across machines / terminal widths.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.commands.imports import _pick_apply_batch_id
from tulip_cli.commands.reconcile import _pick_reconciliation_id
from tulip_cli.commands.transactions import _pick_tx_id
from tulip_cli.config import Config

_API = "https://api.example.com"


def _seeded_token_store(tmp_path: Path) -> TokenStore:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save(
        _API,
        TokenSet(
            email="alice@example.com",
            access_token="access",
            refresh_token="refresh",
            access_expires_at=9_999_999_999,
        ),
    )
    return store


def _config() -> Config:
    return Config(api_url=_API)


def _make_transport_capture(
    handler: dict[str, Any],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Return a transport that records every request it sees.

    ``handler`` maps ``(method, path)`` → response factory or list of dicts.
    Anything not in the map returns 500 so the test fails loudly.
    """
    seen: list[httpx.Request] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = f"{request.method} {request.url.path}"
        if key in handler:
            entry = handler[key]
            return httpx.Response(200, json=entry)
        return httpx.Response(500, json={"unexpected": key})

    return httpx.MockTransport(_dispatch), seen


# ---- imports apply --------------------------------------------------------


def _patch_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Make every ``TulipClient`` constructed during the call use ``transport``.

    The picker resolver helpers build their own ``TulipClient`` instances,
    so we patch the ``httpx.Client`` constructor to thread the mock
    transport through.
    """
    real_init = httpx.Client.__init__

    def _patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_init)


def test_pick_apply_batch_id_returns_first_choice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Numeric ``1`` resolves to the first parsed batch in the list."""
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _make_transport_capture(
        {
            "GET /v1/imports": {
                "items": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "status": "parsed",
                        "source_format": "ofx",
                        "source_filename": "jan.ofx",
                        "imported_count": 3,
                        "skipped_count": 0,
                        "created_at": "2026-01-15T10:00:00",
                    }
                ],
                "next_cursor": None,
            }
        }
    )
    _patch_transport(monkeypatch, transport)

    picked = _pick_apply_batch_id(_config(), as_json=False)
    assert picked == "11111111-1111-1111-1111-111111111111"
    assert any(r.url.path == "/v1/imports" for r in seen)


def test_pick_apply_batch_id_filters_to_parsed_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The picker GET passes ``status=parsed`` so applied batches don't appear."""
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _make_transport_capture(
        {"GET /v1/imports": {"items": [], "next_cursor": None}}
    )
    _patch_transport(monkeypatch, transport)
    _pick_apply_batch_id(_config(), as_json=False)
    [request] = [r for r in seen if r.url.path == "/v1/imports"]
    assert request.url.params.get("status") == "parsed"


def test_pick_apply_batch_id_returns_none_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Typing ``c`` cancels the picker."""
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, _ = _make_transport_capture(
        {
            "GET /v1/imports": {
                "items": [
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "status": "parsed",
                        "source_format": "csv",
                        "source_filename": "x.csv",
                        "imported_count": 1,
                        "skipped_count": 0,
                        "created_at": "2026-02-01T08:00:00",
                    }
                ],
                "next_cursor": None,
            }
        }
    )
    _patch_transport(monkeypatch, transport)
    picked = _pick_apply_batch_id(_config(), as_json=False)
    assert picked is None


def test_pick_apply_batch_id_non_tty_emits_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-interactive stdin → ``None`` + usage-error hint on stderr."""
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    transport, seen = _make_transport_capture({})  # no requests expected
    _patch_transport(monkeypatch, transport)
    picked = _pick_apply_batch_id(_config(), as_json=False)
    assert picked is None
    assert seen == []
    captured = capsys.readouterr()
    assert "Missing argument BATCH_ID" in captured.err


def test_pick_apply_batch_id_json_mode_skips_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` callers always get the usage error, even on a TTY."""
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _make_transport_capture({})
    _patch_transport(monkeypatch, transport)
    picked = _pick_apply_batch_id(_config(), as_json=True)
    assert picked is None
    assert seen == []
    captured = capsys.readouterr()
    assert "Missing argument BATCH_ID" in captured.err


# ---- transactions show ----------------------------------------------------


def test_pick_tx_id_returns_first_choice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _make_transport_capture(
        {
            "GET /v1/transactions": [
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "date": "2026-03-01",
                    "description": "Grocery",
                    "status": "posted",
                }
            ]
        }
    )
    _patch_transport(monkeypatch, transport)
    picked = _pick_tx_id(_config(), as_json=False)
    assert picked == "33333333-3333-3333-3333-333333333333"
    [list_call] = [r for r in seen if r.url.path == "/v1/transactions"]
    # Picker asks for at most 20 rows.
    assert list_call.url.params.get("limit") == "20"


def test_pick_tx_id_returns_none_on_cancel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, _ = _make_transport_capture(
        {
            "GET /v1/transactions": [
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "date": "2026-03-01",
                    "description": "x",
                    "status": "posted",
                }
            ]
        }
    )
    _patch_transport(monkeypatch, transport)
    picked = _pick_tx_id(_config(), as_json=False)
    assert picked is None


def test_pick_tx_id_non_tty_emits_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    transport, seen = _make_transport_capture({})
    _patch_transport(monkeypatch, transport)
    picked = _pick_tx_id(_config(), as_json=False)
    assert picked is None
    assert seen == []
    captured = capsys.readouterr()
    assert "Missing argument TXID" in captured.err


# ---- reconcile show -------------------------------------------------------


def test_pick_reconciliation_id_returns_first_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _make_transport_capture(
        {
            "GET /v1/reconciliations": {
                "items": [
                    {
                        "id": "55555555-5555-5555-5555-555555555555",
                        "account_id": "66666666-6666-6666-6666-666666666666",
                        "statement_period_start": "2026-04-01",
                        "statement_period_end": "2026-04-30",
                        "statement_ending_balance": "1234.56",
                        "status": "in_progress",
                    }
                ]
            }
        }
    )
    _patch_transport(monkeypatch, transport)
    picked = _pick_reconciliation_id(_config(), as_json=False)
    assert picked == "55555555-5555-5555-5555-555555555555"
    [request] = [r for r in seen if r.url.path == "/v1/reconciliations"]
    # Picker filters to in_progress so completed envelopes don't appear.
    assert request.url.params.get("status") == "in_progress"


def test_pick_reconciliation_id_returns_none_on_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, _ = _make_transport_capture(
        {
            "GET /v1/reconciliations": {
                "items": [
                    {
                        "id": "77777777-7777-7777-7777-777777777777",
                        "account_id": "88888888-8888-8888-8888-888888888888",
                        "statement_period_start": "2026-04-01",
                        "statement_period_end": "2026-04-30",
                        "statement_ending_balance": "0.00",
                        "status": "in_progress",
                    }
                ]
            }
        }
    )
    _patch_transport(monkeypatch, transport)
    picked = _pick_reconciliation_id(_config(), as_json=False)
    assert picked is None


def test_pick_reconciliation_id_non_tty_emits_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seeded_token_store(tmp_path)
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    transport, seen = _make_transport_capture({})
    _patch_transport(monkeypatch, transport)
    picked = _pick_reconciliation_id(_config(), as_json=False)
    assert picked is None
    assert seen == []
    captured = capsys.readouterr()
    assert "Missing argument RECONCILIATION_ID" in captured.err


# ---- subprocess smoke: piped stdin → exit 2 + usage error -----------------
#
# These don't need a live API — the picker short-circuits to the usage
# error before any HTTP request when stdin isn't a TTY. We spawn the CLI
# with ``stdin=DEVNULL`` (the default for ``capture_output=True``) so the
# child sees a non-interactive stdin.


def _run_cli_no_tty(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("COLUMNS", "200")
    # Point the CLI at a non-existent URL — we never reach the network
    # because the picker short-circuits on non-TTY stdin first.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            "http://127.0.0.1:1",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def test_imports_apply_missing_arg_non_tty_exits_2() -> None:
    """End-to-end: pipe stdin → usage error, exit 2 (no picker, no HTTP)."""
    result = _run_cli_no_tty("imports", "apply")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Missing argument BATCH_ID" in result.stderr


def test_transactions_show_missing_arg_non_tty_exits_2() -> None:
    """End-to-end: pipe stdin → usage error, exit 2 (no picker, no HTTP)."""
    result = _run_cli_no_tty("transactions", "show")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Missing argument TXID" in result.stderr


def test_reconcile_show_missing_arg_non_tty_exits_2() -> None:
    """End-to-end: pipe stdin → usage error, exit 2 (no picker, no HTTP)."""
    result = _run_cli_no_tty("reconcile", "show")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Missing argument RECONCILIATION_ID" in result.stderr
