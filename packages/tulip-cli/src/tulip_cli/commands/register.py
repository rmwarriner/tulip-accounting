"""``tulip register`` — create a new household and its first user."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Annotated, Final, TextIO

import typer

from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

# Mirrors ``RegisterRequest.password`` ``min_length`` in
# ``tulip-api/schemas/auth.py``. Hardcoded for now because the only
# consumer is this command; once 3+ commands want validation rules we'll
# fetch them from ``/openapi.json`` instead — tracked in #27.
PASSWORD_MIN_LENGTH: Final[int] = 12
_PASSWORD_TOO_SHORT_MESSAGE: Final[str] = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters. Please try again."
)
_PASSWORDS_DIFFER_MESSAGE: Final[str] = "Passwords didn't match. Please try again."


PromptFn = Callable[..., str]
NoticeFn = Callable[[str], None]

_PASSWORD_STDIN_TTY_HINT: Final[str] = (
    "Enter password (input is visible; omit --password-stdin to hide):"  # noqa: S105 — UI hint, not a credential
)


def _acquire_password_interactive(
    *,
    prompt: PromptFn,
    notice: NoticeFn,
) -> str:
    """Prompt for a password (with confirmation), looping until both checks pass.

    The flow gives feedback as early as possible:

    * If the first password is too short, complain and prompt again — no
      confirmation prompt yet, since there's nothing worth confirming.
    * If the confirmation doesn't match, complain and start over.
    * Otherwise return the (validated, confirmed) password.

    ``prompt`` and ``notice`` are injected so the loop is unit-testable
    without driving real terminal I/O.
    """
    while True:
        password = prompt("Password", hide_input=True)
        if len(password) < PASSWORD_MIN_LENGTH:
            notice(_PASSWORD_TOO_SHORT_MESSAGE)
            continue
        confirm = prompt("Repeat for confirmation", hide_input=True)
        if password != confirm:
            notice(_PASSWORDS_DIFFER_MESSAGE)
            continue
        return password


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


def register(
    ctx: typer.Context,
    email: Annotated[str, typer.Option("--email", prompt=True, help="Login email.")],
    display_name: Annotated[
        str,
        typer.Option("--display-name", prompt="Display name", help="Your display name."),
    ],
    household: Annotated[
        str,
        typer.Option("--household", prompt="Household name", help="Household name."),
    ],
    password_stdin: Annotated[
        bool,
        typer.Option(
            "--password-stdin",
            help="Read the password from stdin (one line, no confirmation). For scripts.",
        ),
    ] = False,
) -> None:
    """Create a new household and its first (admin) user."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if password_stdin:
        password = _read_password_from_stdin()
        if len(password) < PASSWORD_MIN_LENGTH:
            _notice(_PASSWORD_TOO_SHORT_MESSAGE)
            raise typer.Exit(1)
    else:
        password = _acquire_password_interactive(prompt=typer.prompt, notice=_notice)

    body = {
        "email": email,
        "password": password,
        "display_name": display_name,
        "household_name": household,
    }
    try:
        with TulipClient(config, as_json=as_json) as client:
            response = client.post("/v1/auth/register", json=body)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(
        f"Registered {email} as {payload.get('role', 'user')} of household {household}.\n"
        "Run `tulip auth login` to sign in.\n"
        "First time here? See docs/QUICKSTART.md for the full setup walkthrough."
    )
