"""``tulip doctor`` — first-run smoke / configuration verification (#135).

Internal-beta self-hosters run this immediately after ``docker compose
up``. The five checks (API reachability, master-key loaded, migration
head, attachment-root writable, token store reachable) cover the
mistakes that would otherwise surface as opaque 500s after the user has
already registered and started entering data.

Exit codes (locked design decision per #135):

* ``0`` — all checks passed.
* ``1`` — at least one warning, no hard failure.
* ``2`` — at least one hard failure.

These semantics override the CLI's general-purpose ``EXIT_*`` constants
(``EXIT_USER=1``, ``EXIT_AUTH=2``) for *this command only*. The doctor's
job is to *diagnose* configuration problems, so a server-side auth
issue here means "your install is broken", not "you typed the wrong
password" — the literal 0/1/2 ladder is what compose / supervisor
scripts expect.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Annotated, Final, Literal

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

#: Severity ordered low → high. ``aggregate_status`` returns the max.
CheckSeverity = Literal["pass", "warn", "fail"]

_SEVERITY_RANK: Final[dict[CheckSeverity, int]] = {"pass": 0, "warn": 1, "fail": 2}
_EXIT_BY_SEVERITY: Final[dict[CheckSeverity, int]] = {"pass": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One row in the doctor's output."""

    name: str
    status: CheckSeverity
    message: str

    def to_dict(self) -> dict[str, str]:
        """Render the result as a JSON-serializable dict for ``--json`` output."""
        return {"name": self.name, "status": self.status, "message": self.message}


def aggregate_status(results: list[CheckResult]) -> CheckSeverity:
    """Return the maximum severity across ``results`` (``"pass"`` if empty)."""
    if not results:
        return "pass"
    return max(results, key=lambda r: _SEVERITY_RANK[r.status]).status


# ---- Individual checks --------------------------------------------------


def _check_api_reachable(client: TulipClient, api_url: str) -> CheckResult:
    name = "API reachability"
    try:
        response = client.get("/health")
    except CliError as err:
        return CheckResult(
            name=name,
            status="fail",
            message=(
                f"GET /health failed: {err.problem.get('detail', 'network error')}. "
                f"Confirm the API is running at {api_url} and reachable from this host."
            ),
        )
    if response.status_code != 200:
        return CheckResult(
            name=name,
            status="fail",
            message=f"GET /health returned {response.status_code}; expected 200.",
        )
    return CheckResult(name=name, status="pass", message="GET /health → 200 OK")


def _check_diagnostics(client: TulipClient) -> tuple[dict[str, object] | None, CheckResult]:
    """Fetch ``/v1/system/diagnostics``; return ``(body, fetch_result)``.

    On fetch failure the body is ``None`` and downstream checks should
    short-circuit with a "skipped — diagnostics unreachable" result.
    """
    name = "Diagnostics endpoint"
    try:
        response = client.get("/v1/system/diagnostics")
    except CliError as err:
        return None, CheckResult(
            name=name,
            status="fail",
            message=(
                f"GET /v1/system/diagnostics failed: "
                f"{err.problem.get('detail', 'network error')}. "
                "Doctor cannot verify the master key, migrations, or attachment root."
            ),
        )
    body = response.json()
    return body, CheckResult(
        name=name, status="pass", message="GET /v1/system/diagnostics → 200 OK"
    )


def _check_master_key(diagnostics: dict[str, object]) -> CheckResult:
    name = "Master-key loaded"
    source = diagnostics.get("master_key_source")
    if source == "ephemeral":
        return CheckResult(
            name=name,
            status="fail",
            message=(
                "API is using an ephemeral master key; field-encrypted columns "
                "(TOTP secrets, attachments) will not survive a restart. Set "
                "TULIP_MASTER_KEY or TULIP_KEY_FILE on the API process and restart."
            ),
        )
    return CheckResult(
        name=name,
        status="pass",
        message=f"master key loaded from {source}",
    )


def _check_migration_head(diagnostics: dict[str, object]) -> CheckResult:
    name = "Migration head"
    if diagnostics.get("alembic_head_match"):
        return CheckResult(
            name=name,
            status="pass",
            message=f"DB at expected revision {diagnostics.get('alembic_head_in_db')}",
        )
    in_db = diagnostics.get("alembic_head_in_db") or "(none)"
    expected = diagnostics.get("alembic_head_expected")
    return CheckResult(
        name=name,
        status="warn",
        message=(
            f"DB at revision {in_db}; this build expects {expected}. "
            "Run `alembic upgrade head` against the deployed database."
        ),
    )


def _check_attachment_root(diagnostics: dict[str, object]) -> CheckResult:
    name = "Attachment root writable"
    if diagnostics.get("attachment_root_writable"):
        return CheckResult(
            name=name,
            status="pass",
            message="API probe wrote and removed a zero-byte file successfully",
        )
    return CheckResult(
        name=name,
        status="fail",
        message=(
            "API cannot create files under the configured attachment root. "
            "Check TULIP_ATTACHMENT_ROOT and the directory's filesystem permissions."
        ),
    )


def _check_token_store(config: Config) -> CheckResult:
    name = "Token store"
    try:
        tokens = default_token_store().load(config.api_url)
    except Exception as exc:
        return CheckResult(
            name=name,
            status="warn",
            message=(
                f"Token store unreachable: {exc}. You'll be unable to log in until "
                "this resolves; set TULIP_TOKEN_STORE to a writable file path as a "
                "workaround."
            ),
        )
    if tokens is None:
        return CheckResult(
            name=name,
            status="warn",
            message=(
                "Token store reachable but empty. Run `tulip auth login` (or "
                "`tulip register` if this is a fresh install) to populate it."
            ),
        )
    return CheckResult(
        name=name,
        status="pass",
        message=f"token store has credentials for {tokens.email}",
    )


# ---- Output rendering ----------------------------------------------------


_GLYPH: Final[dict[CheckSeverity, str]] = {"pass": "✓", "warn": "!", "fail": "✗"}


def _render_human(results: list[CheckResult], overall: CheckSeverity) -> None:
    passed = sum(1 for r in results if r.status == "pass")
    total = len(results)
    if overall == "pass":
        typer.echo(f"tulip doctor: all checks passed ({passed}/{total})")
    elif overall == "warn":
        typer.echo(f"tulip doctor: {passed}/{total} passed — see warnings below")
    else:
        typer.echo(f"tulip doctor: FAILED — {passed}/{total} passed", err=True)

    for result in results:
        glyph = _GLYPH[result.status]
        line = f"  {glyph} {result.name}: {result.message}"
        # Warnings + failures land on stderr so a redirected stdout still
        # gives a clean machine-readable summary on the first line.
        out = sys.stderr if result.status != "pass" else sys.stdout
        out.write(line + "\n")


def _render_json(results: list[CheckResult], overall: CheckSeverity) -> None:
    payload = {
        "overall": overall,
        "exit_code": _EXIT_BY_SEVERITY[overall],
        "checks": [r.to_dict() for r in results],
        "summary": {
            "passed": sum(1 for r in results if r.status == "pass"),
            "warned": sum(1 for r in results if r.status == "warn"),
            "failed": sum(1 for r in results if r.status == "fail"),
            "total": len(results),
        },
    }
    sys.stdout.write(json.dumps(payload) + "\n")


# ---- Entry point ---------------------------------------------------------


def doctor(
    ctx: typer.Context,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="HTTP timeout in seconds for each probe.",
        ),
    ] = 5.0,
) -> None:
    """Verify the configured Tulip install. Run me first if something looks off."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    results: list[CheckResult] = []
    with TulipClient(config, as_json=as_json, timeout=timeout) as client:
        reach = _check_api_reachable(client, config.api_url)
        results.append(reach)
        if reach.status != "fail":
            diagnostics_body, fetch_result = _check_diagnostics(client)
            results.append(fetch_result)
            if diagnostics_body is not None:
                results.append(_check_master_key(diagnostics_body))
                results.append(_check_migration_head(diagnostics_body))
                results.append(_check_attachment_root(diagnostics_body))
    results.append(_check_token_store(config))

    overall = aggregate_status(results)
    if as_json:
        _render_json(results, overall)
    else:
        _render_human(results, overall)
    raise typer.Exit(_EXIT_BY_SEVERITY[overall])
