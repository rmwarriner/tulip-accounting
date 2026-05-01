"""``tulip accounts`` — list and show.

Read-only commands consume ``GET /v1/accounts`` and
``GET /v1/accounts/{id}``. Write paths (``add``, ``deactivate``) land in
P3.4 (#21).

The ``show`` command resolves an identifier as a UUID first and falls
back to a code lookup over the listed accounts. ``code`` has no
uniqueness constraint server-side, so duplicates produce an ambiguous-id
error rather than silently picking the first match.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_USER, CliError
from tulip_cli.http import TulipClient

accounts_app = typer.Typer(
    name="accounts",
    help="List and inspect accounts.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _ambiguous_code_problem(identifier: str, count: int) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/account.ambiguous_code",
        "title": "Account code matches multiple accounts",
        "status": 0,
        "detail": (
            f"{count} accounts have code {identifier!r}. "
            "Use the account UUID to disambiguate, or rename one of the duplicates."
        ),
        "instance": "",
        "code": "account.ambiguous_code",
    }


def _not_found_problem(identifier: str) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/account.not_found",
        "title": "Account not found",
        "status": 0,
        "detail": (
            f"No account with code or id {identifier!r} is visible to this user. "
            "Run `tulip accounts list` to see what's available."
        ),
        "instance": "",
        "code": "account.not_found",
    }


def _resolve_account(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single account dict by UUID or by ``code``.

    UUID-shaped strings are looked up via ``GET /v1/accounts/{id}``.
    Anything else is matched against the listed accounts' ``code`` field.
    """
    try:
        UUID(identifier)
    except ValueError:
        # Not a UUID — fall through to code lookup.
        pass
    else:
        response = client.get(f"/v1/accounts/{identifier}", authenticated=True)
        return dict(response.json())

    response = client.get("/v1/accounts", authenticated=True)
    accounts = response.json()
    matches = [a for a in accounts if a.get("code") == identifier]
    if not matches:
        raise CliError(
            problem=_not_found_problem(identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(matches) > 1:
        raise CliError(
            problem=_ambiguous_code_problem(identifier, len(matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    return dict(matches[0])


def _render_table(accounts: list[dict[str, Any]]) -> None:
    """Render a list of account dicts as a Rich table to stdout."""
    table = Table(show_header=True, show_lines=False)
    table.add_column("code")
    table.add_column("name")
    table.add_column("type")
    table.add_column("currency")
    table.add_column("visibility")
    for a in accounts:
        table.add_row(
            a.get("code") or "—",
            a.get("name") or "",
            a.get("type") or "",
            a.get("currency") or "",
            a.get("visibility") or "",
        )
    Console().print(table)


def _render_tree(accounts: list[dict[str, Any]]) -> None:
    """Render the account list as a tree grouped by ``parent_account_id``."""
    by_id = {a["id"]: a for a in accounts}
    children_by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for a in accounts:
        parent_id = a.get("parent_account_id")
        children_by_parent.setdefault(parent_id, []).append(a)

    def _label(a: dict[str, Any]) -> str:
        code = a.get("code") or "—"
        name = a.get("name") or ""
        atype = a.get("type") or ""
        return f"{code}  [dim]{name} · {atype}[/dim]"

    def _attach(node: Tree, account: dict[str, Any]) -> None:
        for child in sorted(
            children_by_parent.get(account["id"], []),
            key=lambda c: (c.get("code") or "", c.get("name") or ""),
        ):
            child_node = node.add(_label(child))
            _attach(child_node, child)

    root = Tree("[bold]accounts[/bold]")
    top_level = sorted(
        children_by_parent.get(None, []),
        key=lambda c: (c.get("code") or "", c.get("name") or ""),
    )
    # Also include any accounts whose declared parent isn't in our list
    # (orphans — shouldn't happen given the API's role filtering, but
    # render them anyway so we don't silently swallow rows).
    orphans = [
        a
        for a in accounts
        if a.get("parent_account_id") is not None and a.get("parent_account_id") not in by_id
    ]
    for a in top_level:
        node = root.add(_label(a))
        _attach(node, a)
    for a in orphans:
        node = root.add(_label(a) + " [yellow](parent not visible)[/yellow]")
        _attach(node, a)
    Console().print(root)


def _has_nesting(accounts: list[dict[str, Any]]) -> bool:
    return any(a.get("parent_account_id") for a in accounts)


def _render_account(account: dict[str, Any], parent: dict[str, Any] | None = None) -> None:
    """Render a single account dict as a vertical key/value list.

    If ``parent`` is provided, the parent's code/name are surfaced inline
    so users don't have to mentally resolve a UUID.
    """
    typer.echo(f"id:           {account.get('id', '')}")
    typer.echo(f"code:         {account.get('code') or '—'}")
    typer.echo(f"name:         {account.get('name', '')}")
    typer.echo(f"type:         {account.get('type', '')}")
    if account.get("subtype"):
        typer.echo(f"subtype:      {account['subtype']}")
    typer.echo(f"currency:     {account.get('currency', '')}")
    typer.echo(f"visibility:   {account.get('visibility', '')}")
    typer.echo(f"is_active:    {account.get('is_active', '')}")
    if account.get("parent_account_id"):
        if parent is not None:
            parent_label = f"{parent.get('code') or '—'} ({parent.get('name', '')})"
            typer.echo(f"parent:       {parent_label} [{account['parent_account_id']}]")
        else:
            typer.echo(f"parent:       {account['parent_account_id']}")


@accounts_app.command("list")
def list_accounts(
    ctx: typer.Context,
    flat: Annotated[
        bool,
        typer.Option(
            "--flat",
            help="Force the flat table view instead of the default tree.",
        ),
    ] = False,
) -> None:
    """List active accounts visible to the logged-in user.

    Default rendering is a tree when any account has a parent; otherwise
    a flat table. ``--flat`` forces the table view (useful for scripts).
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/accounts", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    accounts = response.json()
    if not accounts:
        typer.echo("No accounts. Run `tulip accounts add` to create one.")
        return
    if flat or not _has_nesting(accounts):
        _render_table(accounts)
    else:
        _render_tree(accounts)


@accounts_app.command("add")
def add_account(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Display name.")],
    type_: Annotated[
        str,
        typer.Option(
            "--type",
            help="One of: asset, liability, equity, income, expense.",
        ),
    ],
    currency: Annotated[
        str,
        typer.Option(
            "--currency",
            help="ISO 4217 three-letter code (e.g. USD).",
        ),
    ],
    code: Annotated[
        str | None,
        typer.Option(
            "--code",
            help="Optional short code (e.g. assets:checking).",
        ),
    ] = None,
    subtype: Annotated[
        str | None,
        typer.Option("--subtype", help="Optional subtype label."),
    ] = None,
    visibility: Annotated[
        str,
        typer.Option(
            "--visibility",
            help="'shared' (default) or 'private'.",
        ),
    ] = "shared",
    parent: Annotated[
        str | None,
        typer.Option(
            "--parent",
            help=(
                "Optional parent account (code or UUID). The parent's type "
                "and currency must match this account, and a private parent "
                "forces a private child."
            ),
        ),
    ] = None,
) -> None:
    """Create a new account in the logged-in user's household."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "name": name,
        "type": type_,
        "currency": currency,
        "visibility": visibility,
    }
    if code is not None:
        body["code"] = code
    if subtype is not None:
        body["subtype"] = subtype

    try:
        with _client(config, as_json=as_json) as client:
            if parent is not None:
                resolved = _resolve_account(client, parent)
                body["parent_account_id"] = resolved["id"]
            response = client.post("/v1/accounts", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"Created account {payload.get('id', '')}")
    _render_account(payload)


@accounts_app.command("edit")
def edit_account(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(
            help="Account code (e.g. assets:checking) or UUID.",
            metavar="ACCOUNT",
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="New display name."),
    ] = None,
    code: Annotated[
        str | None,
        typer.Option("--code", help="New short code."),
    ] = None,
    subtype: Annotated[
        str | None,
        typer.Option("--subtype", help="New subtype label."),
    ] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="'shared' or 'private'."),
    ] = None,
    parent: Annotated[
        str | None,
        typer.Option(
            "--parent",
            help=(
                "New parent account (code or UUID). The same parent-validation "
                "rules from `accounts add` apply (type/currency match, "
                "visibility, no cycles)."
            ),
        ),
    ] = None,
) -> None:
    """Update mutable fields on an existing account.

    Only flags that are explicitly passed are sent — PATCH semantics, so
    omitted fields stay as-is. Resolves the target via the same UUID-or-
    code lookup as ``show``.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if code is not None:
        body["code"] = code
    if subtype is not None:
        body["subtype"] = subtype
    if visibility is not None:
        body["visibility"] = visibility

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_account(client, identifier)
            if parent is not None:
                resolved_parent = _resolve_account(client, parent)
                body["parent_account_id"] = resolved_parent["id"]
            response = client.patch(f"/v1/accounts/{target['id']}", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"Updated account {payload.get('id', '')}")
    _render_account(payload)


@accounts_app.command("deactivate")
def deactivate_account(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(
            help="Account code (e.g. assets:checking) or UUID.",
            metavar="ACCOUNT",
        ),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Soft-delete (deactivate) an account.

    The account stays in the database (audit trail) but disappears from
    ``accounts list``. Admin-only on the API side. By default prompts for
    confirmation; ``--yes`` skips for scripts.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_account(client, identifier)
            if not yes:
                label = target.get("code") or target.get("name") or str(target["id"])
                if not typer.confirm(
                    f"Deactivate account {label}? It will disappear from `accounts list`.",
                    default=False,
                ):
                    typer.echo("Aborted; no changes made.")
                    return
            client.delete(f"/v1/accounts/{target['id']}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        # The API returns 204 No Content; emit an explicit body for scripts.
        sys.stdout.write(json.dumps({"deactivated": str(target["id"])}) + "\n")
        return
    typer.echo(f"Deactivated account {target.get('code') or target['id']}.")


@accounts_app.command("show")
def show_account(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(
            help="Account code (e.g. assets:checking) or UUID.",
            metavar="ACCOUNT",
        ),
    ],
) -> None:
    """Show one account by code or UUID."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    parent: dict[str, Any] | None = None
    try:
        with _client(config, as_json=as_json) as client:
            account = _resolve_account(client, identifier)
            parent_id = account.get("parent_account_id")
            if parent_id:
                # Fetch the parent to surface its code/name. A 404 here
                # would be unexpected (the API just returned this child)
                # but if it happens we render the child without the
                # enriched parent line rather than crashing.
                try:
                    parent_response = client.get(f"/v1/accounts/{parent_id}", authenticated=True)
                    parent = dict(parent_response.json())
                except CliError:
                    parent = None
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(json.dumps(account) + "\n")
        return
    _render_account(account, parent=parent)
