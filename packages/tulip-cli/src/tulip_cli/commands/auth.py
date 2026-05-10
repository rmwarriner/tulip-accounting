"""``tulip auth`` — login (with MFA + recovery), logout, status."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Final, TextIO

import typer

from tulip_cli.auth.jwt_decode import decode_jwt_payload
from tulip_cli.auth.tokens import TokenSet, default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_AUTH, EXIT_OK, CliError
from tulip_cli.http import TulipClient

auth_app = typer.Typer(
    name="auth",
    help="Authentication: login, logout, status.",
    no_args_is_help=True,
)

_MFA_REQUIRED_CODE: Final[str] = "auth.mfa_required"
_MFA_ENROLLMENT_REQUIRED_CODE: Final[str] = "auth.mfa_enrollment_required"

_PASSWORD_STDIN_TTY_HINT: Final[str] = (
    "Enter password (input is visible; omit --password-stdin to hide):"  # noqa: S105 — UI hint, not a credential
)

NoticeFn = Callable[[str], None]


def _notice(message: str) -> None:
    typer.echo(message, err=True)


def _read_password_from_stdin(
    *,
    stream: TextIO | None = None,
    notice: NoticeFn = _notice,
) -> str:
    """Read one line of password from ``stream`` (default: real stdin).

    When ``stream`` is a TTY (no redirection), emit a one-line hint to
    ``notice`` first — without it, the CLI sits silently waiting and
    looks hung. Pipe / heredoc callers (the script-friendly path) see no
    extra output.
    """
    src = sys.stdin if stream is None else stream
    if src.isatty():
        notice(_PASSWORD_STDIN_TTY_HINT)
    return src.readline().rstrip("\n")


def _store_tokens(config: Config, email: str, body: dict[str, Any]) -> TokenSet:
    """Persist a TokenResponse body under the configured API URL."""
    tokens = TokenSet(
        email=email,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        access_expires_at=int(time.time()) + int(body["expires_in"]),
    )
    default_token_store().save(config.api_url, tokens)
    return tokens


@auth_app.command("login")
def login(
    ctx: typer.Context,
    email: Annotated[str, typer.Option("--email", prompt=True, help="Login email.")],
    password_stdin: Annotated[
        bool,
        typer.Option(
            "--password-stdin",
            help="Read the password from stdin (one line). For scripts.",
        ),
    ] = False,
    use_recovery_code: Annotated[
        bool,
        typer.Option(
            "--recovery",
            help="On the MFA challenge, prompt for a recovery code instead of a TOTP code.",
        ),
    ] = False,
    code_stdin: Annotated[
        bool,
        typer.Option(
            "--code-stdin",
            help=(
                "When the API issues an MFA challenge, read the TOTP / recovery code "
                "from the line of stdin after the password. For scripts."
            ),
        ),
    ] = False,
) -> None:
    """Authenticate against the configured API. Stores tokens on success."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if password_stdin:
        password = _read_password_from_stdin()
    else:
        password = typer.prompt("Password", hide_input=True)

    body = {"email": email, "password": password}
    try:
        with TulipClient(config, as_json=as_json) as client:
            response = client.post("/v1/auth/login", json=body)
    except CliError as err:
        if err.problem.get("code") == _MFA_REQUIRED_CODE:
            tokens = _complete_mfa_challenge(
                config=config,
                as_json=as_json,
                email=email,
                challenge_problem=err.problem,
                use_recovery_code=use_recovery_code,
                code_stdin=code_stdin,
            )
            _emit_login_success(email, tokens, as_json=as_json)
            return
        if err.problem.get("code") == _MFA_ENROLLMENT_REQUIRED_CODE:
            err.render()
            enrollment_url = err.problem.get("enrollment_url")
            if isinstance(enrollment_url, str) and not as_json:
                typer.echo(
                    f"  Enrollment endpoint: {config.api_url}{enrollment_url}",
                    err=True,
                )
            raise typer.Exit(EXIT_AUTH) from None
        err.render()
        raise typer.Exit(err.exit_code) from None

    tokens = _store_tokens(config, email, response.json())
    _emit_login_success(email, tokens, as_json=as_json, response_text=response.text)


def _complete_mfa_challenge(
    *,
    config: Config,
    as_json: bool,
    email: str,
    challenge_problem: dict[str, Any],
    use_recovery_code: bool,
    code_stdin: bool,
) -> TokenSet:
    """Step 2 of an MFA-gated login: submit the code, get tokens."""
    mfa_token = challenge_problem.get("mfa_token")
    if not isinstance(mfa_token, str) or not mfa_token:
        # Defensive: API contract says this is always present; if it
        # isn't we surface the original problem rather than guess.
        raise CliError(problem=challenge_problem, as_json=as_json, exit_code=EXIT_AUTH)

    if code_stdin:
        code = sys.stdin.readline().rstrip("\n")
    else:
        prompt = "Recovery code" if use_recovery_code else "Authenticator code"
        code = typer.prompt(prompt)

    if use_recovery_code:
        path = "/v1/auth/login/recover"
        body: dict[str, Any] = {"mfa_token": mfa_token, "recovery_code": code}
    else:
        path = "/v1/auth/login/mfa"
        body = {"mfa_token": mfa_token, "code": code}

    try:
        with TulipClient(config, as_json=as_json) as client:
            response = client.post(path, json=body)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    return _store_tokens(config, email, response.json())


def _emit_login_success(
    email: str,
    tokens: TokenSet,
    *,
    as_json: bool,
    response_text: str | None = None,
) -> None:
    if as_json and response_text is not None:
        sys.stdout.write(response_text + "\n")
        return
    if as_json:
        sys.stdout.write('{"access_token":"' + tokens.access_token + '","email":"' + email + '"}\n')
        return
    typer.echo(f"Logged in as {email}.")


@auth_app.command("logout")
def logout(ctx: typer.Context) -> None:
    """Revoke the stored refresh token and clear local tokens."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    store = default_token_store()
    tokens = store.load(config.api_url)
    if tokens is None:
        if as_json:
            sys.stdout.write('{"already_logged_out":true}\n')
        else:
            typer.echo("Already logged out.")
        raise typer.Exit(EXIT_OK)
    try:
        with TulipClient(config, as_json=as_json) as client:
            client.post("/v1/auth/logout", json={"refresh_token": tokens.refresh_token})
    except CliError as err:
        # Even if the API call fails, drop the local tokens — better
        # than leaving the user "half logged in" with a token they
        # can't refresh.
        store.clear(config.api_url)
        err.render()
        raise typer.Exit(err.exit_code) from None
    store.clear(config.api_url)
    if as_json:
        sys.stdout.write('{"logged_out":true}\n')
    else:
        typer.echo("Logged out.")


@auth_app.command("status")
def status(ctx: typer.Context) -> None:
    """Display the current login state for the configured API URL.

    Reads tokens from the local store and decodes the access-token JWT
    payload locally — *no network call*. The displayed claims are
    descriptive, not validated; a revoked token still "looks logged in"
    here until the next real command. Validating against the API will
    move under a ``--check`` flag once ``GET /v1/auth/me`` ships (#24).
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    tokens = default_token_store().load(config.api_url)
    if tokens is None:
        if as_json:
            sys.stdout.write('{"logged_in":false}\n')
        else:
            typer.echo(f"Not logged in at {config.api_url}.")
        raise typer.Exit(EXIT_OK)

    claims = decode_jwt_payload(tokens.access_token) or {}
    expires_in = tokens.access_expires_at - int(time.time())
    if as_json:
        import json as _json

        sys.stdout.write(
            _json.dumps(
                {
                    "logged_in": True,
                    "api_url": config.api_url,
                    "email": tokens.email,
                    "user_id": claims.get("sub"),
                    "household_id": claims.get("household_id"),
                    "role": claims.get("role"),
                    "access_expires_at": tokens.access_expires_at,
                    "access_expires_in_seconds": expires_in,
                }
            )
            + "\n"
        )
        return

    expires_at = datetime.fromtimestamp(tokens.access_expires_at, tz=UTC)
    typer.echo(f"Logged in at {config.api_url}")
    typer.echo(f"  email:        {tokens.email}")
    if claims.get("household_id"):
        typer.echo(f"  household_id: {claims['household_id']}")
    if claims.get("role"):
        typer.echo(f"  role:         {claims['role']}")
    if expires_in <= 0:
        typer.echo("  access token: expired (next command will auto-refresh)")
    else:
        mins = max(1, expires_in // 60)
        typer.echo(
            f"  access token: valid for ~{mins} more minute(s) "
            f"(expires {expires_at.isoformat(timespec='seconds')})"
        )
