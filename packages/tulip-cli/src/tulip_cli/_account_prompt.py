"""Interactive create-missing-account prompt for the import commands (#196).

Used by ``tulip imports {ofx,qif,csv}`` when ``_resolve_account`` raises
``account.not_found``: at a TTY (and not in ``--json`` mode) we offer to
create the missing account inline rather than forcing the user to switch
terminals to ``tulip accounts add``. Scripts, CI, and JSON callers get
``None`` immediately so they hit the legacy fail-fast path unchanged.

Smart defaults from the identifier the user originally passed:

* purely numeric (``"1010"``) → use as the account ``code``; prompt for
  the human-readable ``name``.
* colon-path (``"assets:checking"``) → use as the ``code`` and default
  the ``name`` to the leaf segment.
* anything else → default the ``name`` to the identifier verbatim; no
  ``code`` default.

The helper does not retry on API failures (e.g., duplicate code, invalid
parent) — those bubble out as ``CliError`` to the caller, which decides
whether to fall back to the original error path.
"""

from __future__ import annotations

from typing import Any, Final

import typer

from tulip_cli._picker import is_interactive
from tulip_cli.http import TulipClient

#: Account types accepted by the API. The prompt re-asks on any other
#: value so we never POST a body that the server will reject for a
#: trivially-fixable reason.
_VALID_TYPES: Final[frozenset[str]] = frozenset(
    {"asset", "liability", "equity", "income", "expense"}
)


def prompt_create_missing_account(
    client: TulipClient,
    identifier: str,
    *,
    as_json: bool,
) -> dict[str, Any] | None:
    """Offer to create ``identifier`` as a new account; return it on success.

    Returns ``None`` when:

    * stdin is not a TTY (``is_interactive() is False``),
    * the caller is in ``--json`` mode (``as_json=True``),
    * the user declines the create-it prompt or sends EOF.

    On success the returned dict is the API's ``AccountRead`` body — the
    caller uses ``["id"]`` for the retry. Any API failure during the
    inline create raises ``CliError`` (the caller can decide to fall
    through to the original ``account.unknown`` rendering).
    """
    if as_json or not is_interactive():
        return None

    default_name, default_code = _smart_defaults(identifier)

    try:
        confirm = typer.confirm(
            f"Account {identifier!r} not found. Create it now?",
            default=True,
        )
    except (typer.Abort, EOFError):
        return None
    if not confirm:
        return None

    try:
        name = typer.prompt("  name", default=default_name).strip()
        type_ = _prompt_account_type()
        currency = typer.prompt("  currency", default="USD").strip().upper()
        code = typer.prompt(
            "  code (blank for none)",
            default=default_code or "",
            show_default=bool(default_code),
        ).strip()
    except (typer.Abort, EOFError):
        return None

    body: dict[str, Any] = {
        "name": name,
        "type": type_,
        "currency": currency,
        "visibility": "shared",
    }
    if code:
        body["code"] = code

    response = client.post("/v1/accounts", json=body, authenticated=True)
    return dict(response.json())


def _smart_defaults(identifier: str) -> tuple[str, str | None]:
    """Pick ``(default_name, default_code)`` from the failing identifier."""
    if not identifier:
        return ("", None)
    if identifier.isdigit():
        # Looks like an account number; surface it as the code, leave
        # the name blank so the user types something human-readable.
        return ("", identifier)
    if ":" in identifier:
        leaf = identifier.split(":")[-1].strip()
        return (leaf, identifier)
    return (identifier, None)


def _prompt_account_type() -> str:
    """Re-prompt until the user types one of the accepted account types."""
    while True:
        try:
            raw: str = typer.prompt(
                "  type (asset / liability / equity / income / expense)",
            )
        except (typer.Abort, EOFError) as exc:
            # Propagate so the caller can return ``None`` rather than
            # looping forever on a closed stdin.
            raise EOFError from exc
        value = raw.strip().lower()
        if value in _VALID_TYPES:
            return value
        typer.echo(
            f"  '{value}' is not a valid account type — try one of "
            "asset / liability / equity / income / expense.",
            err=True,
        )


__all__: list[str] = ["prompt_create_missing_account"]
