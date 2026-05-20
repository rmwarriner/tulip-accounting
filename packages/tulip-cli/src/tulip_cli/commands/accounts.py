"""``tulip accounts`` — list and show.

Read-only commands consume ``GET /v1/accounts`` and
``GET /v1/accounts/{id}``. Write paths (``add``, ``deactivate``) land in
P3.4 (#21).

The ``show`` command (and every ``--account`` surface, via
:func:`_resolve_account`) resolves an identifier in this order: UUID,
exact ``code``, unique ``name``, hierarchical colon-path. ``code`` and
``name`` have no uniqueness constraint server-side, so duplicates
produce an ambiguous-identifier error rather than silently picking the
first match. See #197.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.table import Table
from rich.tree import Tree

from tulip_cli._account_path import split_path
from tulip_cli._console import make_console
from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_USER, CliError
from tulip_cli.gnucash import (
    GnuCashParseError,
    sort_by_depth,
)
from tulip_cli.gnucash import (
    parse as gnucash_parse,
)
from tulip_cli.http import TulipClient

accounts_app = typer.Typer(
    name="accounts",
    help="List and inspect accounts.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


#: Account-type tokens accepted as the leading segment of a hierarchical
#: path, mapped to the canonical singular the API stores. Lets a user
#: type the plural form they see in journal exports (``assets:cash``)
#: or the singular (``asset:cash``); both resolve. See #197.
_TYPE_ALIASES: dict[str, str] = {
    "asset": "asset",
    "assets": "asset",
    "liability": "liability",
    "liabilities": "liability",
    "equity": "equity",
    "equities": "equity",
    "income": "income",
    "incomes": "income",
    "expense": "expense",
    "expenses": "expense",
}


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


def _ambiguous_name_problem(identifier: str, full_paths: list[str]) -> dict[str, object]:
    listed = "\n  ".join(sorted(full_paths))
    return {
        "type": "/.well-known/errors/account.ambiguous_name",
        "title": "Account identifier matches multiple accounts",
        "status": 0,
        "detail": (
            f"{len(full_paths)} accounts match {identifier!r}. "
            "Disambiguate with a fuller hierarchical path, the account "
            f"code, or the UUID:\n  {listed}"
        ),
        "instance": "",
        "code": "account.ambiguous_name",
    }


def _not_found_problem(identifier: str) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/account.not_found",
        "title": "Account not found",
        "status": 0,
        "detail": (
            f"No account matching {identifier!r} (by id, code, name, or "
            "hierarchical path) is visible to this user. "
            "Run `tulip accounts list` to see what's available."
        ),
        "instance": "",
        "code": "account.not_found",
    }


def _account_name_path(account: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """Return the lowercased ``[root_name, …, leaf_name]`` chain for an account.

    Walks ``parent_account_id`` to the root. The ``seen`` guard is purely
    defensive — the server enforces a tree, but a malformed response
    shouldn't hang the CLI.
    """
    names: list[str] = []
    seen: set[str] = set()
    cur: dict[str, Any] | None = account
    while cur is not None:
        cur_id = str(cur["id"])
        if cur_id in seen:
            break
        seen.add(cur_id)
        names.append(str(cur["name"]).lower())
        parent_id = cur.get("parent_account_id")
        cur = by_id.get(str(parent_id)) if parent_id else None
    names.reverse()
    return names


def _account_full_path(account: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    """Render an account's ``type:name:…:name`` path for error messages.

    Uses the original-case names so the printed path is something the
    user can read back; the type segment stays lowercased to match what
    the API stores.
    """
    names: list[str] = []
    seen: set[str] = set()
    cur: dict[str, Any] | None = account
    while cur is not None:
        cur_id = str(cur["id"])
        if cur_id in seen:
            break
        seen.add(cur_id)
        names.append(str(cur["name"]))
        parent_id = cur.get("parent_account_id")
        cur = by_id.get(str(parent_id)) if parent_id else None
    names.reverse()
    return ":".join([str(account["type"]).lower(), *names])


def _match_name_or_path(accounts: list[dict[str, Any]], identifier: str) -> list[dict[str, Any]]:
    r"""Return accounts matching ``identifier`` as a name or hierarchical path.

    The identifier is split on ``:`` into case-insensitive segments
    via :func:`tulip_cli._account_path.split_path`, which honours
    backslash escapes — ``Hardware\:Drills`` resolves as a single
    segment to an account literally named ``Hardware: Drills`` (#416,
    closing the round trip with the output-side path renderer in
    #300). A leading segment that names an account type
    (``assets``/``asset``…) constrains the match to that type; the
    remaining segments must be a suffix of the candidate's root→leaf
    name chain. A single segment is just a plain (unique) name
    lookup.

    Empty segments (``::``, a trailing ``:``) make the identifier
    un-resolvable as a path — returns no matches so the caller falls
    through to the not-found error.
    """
    parsed = split_path(identifier)
    if parsed is None:
        return []
    tokens = [seg.lower() for seg in parsed]

    type_constraint: str | None = None
    name_tokens = tokens
    if len(tokens) > 1 and tokens[0] in _TYPE_ALIASES:
        type_constraint = _TYPE_ALIASES[tokens[0]]
        name_tokens = tokens[1:]
    if not name_tokens:
        return []

    by_id = {str(a["id"]): a for a in accounts}
    matches: list[dict[str, Any]] = []
    for account in accounts:
        if type_constraint is not None and str(account.get("type", "")).lower() != type_constraint:
            continue
        name_path = _account_name_path(account, by_id)
        if len(name_path) >= len(name_tokens) and name_path[-len(name_tokens) :] == name_tokens:
            matches.append(account)
    return matches


def _resolve_account(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single account dict by UUID, code, name, or hierarchical path.

    Resolution order (#197):

    1. UUID — ``GET /v1/accounts/{id}``.
    2. Exact ``code`` match over the listed accounts. Duplicate codes
       raise ``account.ambiguous_code``; a no-match falls through.
    3. Unique ``name`` / hierarchical colon-path match (case-insensitive,
       type-prefix optional). Multiple matches raise
       ``account.ambiguous_name`` with the full paths listed.
    4. Nothing matched — ``account.not_found``.
    """
    try:
        UUID(identifier)
    except ValueError:
        # Not a UUID — fall through to code / name / path lookup.
        pass
    else:
        response = client.get(f"/v1/accounts/{identifier}", authenticated=True)
        return dict(response.json())

    response = client.get("/v1/accounts", authenticated=True)
    accounts = response.json()

    # 2. Exact code match. Ambiguous codes are a hard error; a clean miss
    #    falls through to name / path resolution.
    code_matches = [a for a in accounts if a.get("code") == identifier]
    if len(code_matches) > 1:
        raise CliError(
            problem=_ambiguous_code_problem(identifier, len(code_matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(code_matches) == 1:
        return dict(code_matches[0])

    # 3. Name / hierarchical-path resolution.
    name_matches = _match_name_or_path(accounts, identifier)
    if len(name_matches) > 1:
        by_id = {str(a["id"]): a for a in accounts}
        raise CliError(
            problem=_ambiguous_name_problem(
                identifier, [_account_full_path(a, by_id) for a in name_matches]
            ),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(name_matches) == 1:
        return dict(name_matches[0])

    # 4. Nothing matched.
    raise CliError(
        problem=_not_found_problem(identifier),
        as_json=False,
        exit_code=EXIT_USER,
    )


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
    make_console().print(table)


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
    make_console().print(root)


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
    if account.get("is_placeholder"):
        typer.echo("placeholder:  true (rejects postings)")
    if account.get("parent_account_id"):
        if parent is not None:
            parent_label = f"{parent.get('code') or '—'} ({parent.get('name', '')})"
            typer.echo(f"parent:       {parent_label} [{account['parent_account_id']}]")
        else:
            typer.echo(f"parent:       {account['parent_account_id']}")
    notes = account.get("notes")
    if notes:
        # Multi-line indent so long notes still read cleanly.
        first, *rest = notes.splitlines() or [""]
        typer.echo(f"notes:        {first}")
        for line in rest:
            typer.echo(f"              {line}")


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
                "Optional parent account. Accepts UUID, code, name, or "
                "hierarchical path (e.g. 'Assets:Current Assets'). The "
                "parent's type and currency must match this account, and a "
                "private parent forces a private child."
            ),
        ),
    ] = None,
    create_parents: Annotated[
        bool,
        typer.Option(
            "--create-parents",
            help=(
                "Parse --name OR --code as a colon-delimited path and "
                "auto-create any missing ancestor accounts in one atomic "
                "call. Name-path form (PTA / Quicken convention, #416): "
                "--name 'Assets:Current Assets:Checking' creates segments "
                "as proper display names with code=None on each. Code-"
                "path form (#46): --code 'assets:current:checking' uses "
                "the segments for both name and code. The root segment "
                "determines the type — it must match --type. Mutually "
                "exclusive with --parent (parents come from the path)."
            ),
        ),
    ] = False,
    notes: Annotated[
        str | None,
        typer.Option(
            "--notes",
            help=(
                "Optional freeform comment / context (#50). Stored "
                "field-encrypted under the household master key."
            ),
        ),
    ] = None,
    placeholder: Annotated[
        bool,
        typer.Option(
            "--placeholder",
            help=(
                "Mark this account as a non-posting organisational node "
                "(#52). The API rejects any posting whose target is a "
                "placeholder; useful for chart-of-accounts headers."
            ),
        ),
    ] = False,
) -> None:
    """Create a new account in the logged-in user's household.

    With ``--create-parents``, either ``--name`` or ``--code`` is
    parsed as a colon-path and any missing ancestors are auto-created
    in the same atomic request. Passing colons in both ``--name`` and
    ``--code`` is rejected as ambiguous.
    """
    if create_parents and parent is not None:
        raise typer.BadParameter(
            "--create-parents derives the parent chain from --name or "
            "--code; passing --parent at the same time is ambiguous"
        )
    name_is_path = ":" in (name or "")
    code_is_path = ":" in (code or "")
    if create_parents and name_is_path and code_is_path:
        raise typer.BadParameter(
            "--create-parents: --name and --code both contain colons; "
            "pass the hierarchy in one or the other, not both"
        )
    if create_parents and not name_is_path and not code_is_path:
        raise typer.BadParameter(
            "--create-parents needs a colon-path in --name (e.g. "
            "'Assets:Current Assets:Checking') or --code (e.g. "
            "'assets:current:checking')"
        )

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
    if notes is not None:
        body["notes"] = notes
    if placeholder:
        body["is_placeholder"] = True
    if create_parents:
        body["create_parents"] = True

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
    parents_created = payload.get("parents_created") or []
    if parents_created:
        typer.echo(
            f"Created account {payload.get('id', '')} along with {len(parents_created)} parent(s):"
        )
        for parent_account in parents_created:
            typer.echo(
                f"  + {parent_account.get('code') or '—'}  ({parent_account.get('name', '')})"
            )
        typer.echo("Leaf:")
    else:
        typer.echo(f"Created account {payload.get('id', '')}")
    _render_account(payload)


@accounts_app.command("edit")
def edit_account(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(
            help=(
                "Account UUID, code, name, or hierarchical path "
                "(e.g. 'Assets:Current Assets:Checking')."
            ),
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
                "New parent account. Accepts UUID, code, name, or "
                "hierarchical path. The same parent-validation "
                "rules from `accounts add` apply (type/currency match, "
                "visibility, no cycles)."
            ),
        ),
    ] = None,
    notes: Annotated[
        str | None,
        typer.Option(
            "--notes",
            help=(
                "Set the freeform notes / comments field (#50). Pass "
                "an empty string to clear an existing note."
            ),
        ),
    ] = None,
    placeholder: Annotated[
        bool | None,
        typer.Option(
            "--placeholder/--no-placeholder",
            help=(
                "Toggle the placeholder flag (#52). Flipping to "
                "placeholder requires the account to have no existing "
                "postings."
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
    if notes is not None:
        body["notes"] = notes
    if placeholder is not None:
        body["is_placeholder"] = placeholder

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
            help=(
                "Account UUID, code, name, or hierarchical path "
                "(e.g. 'Assets:Current Assets:Checking')."
            ),
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
            help=(
                "Account UUID, code, name, or hierarchical path "
                "(e.g. 'Assets:Current Assets:Checking')."
            ),
            metavar="ACCOUNT",
        ),
    ],
) -> None:
    """Show one account by UUID, code, name, or hierarchical path."""
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


# ---- #432: GnuCash CSV account-tree import ---------------------------------


@accounts_app.command("import-gnucash")
def import_gnucash(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            help="Path to a GnuCash 'Export Account Tree to CSV' file.",
            metavar="PATH",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Parse the file + print a plan, but make no API calls. "
                "First-cut for any real migration so the operator can "
                "see the type mapping + warnings before mutating data."
            ),
        ),
    ] = False,
    default_currency: Annotated[
        str,
        typer.Option(
            "--default-currency",
            help=(
                "Currency for non-CURRENCY-namespace rows (STOCK / MUTUAL "
                "with a ticker symbol). The original Symbol / Namespace is "
                "stashed in the account's notes field so the operator can "
                "find these later when investment tracking lands."
            ),
        ),
    ] = "USD",
) -> None:
    """Import a GnuCash account-tree CSV into the household's chart.

    The CSV comes from GnuCash's *File → Export → Export Account
    Tree to CSV* command. The importer:

    1. Parses every row + maps the GnuCash Type to a Tulip type + subtype.
    2. Sorts by depth so parents are POSTed before children.
    3. For each row, resolves the parent via the colon-path prefix,
       checks for an existing account with the same code+name (skip
       if found — re-runs are idempotent), then POSTs to /v1/accounts.
    4. Prints a per-row outcome summary at the end.

    Lands ``Notes`` (or ``Description`` when Notes is blank) into Tulip's
    notes field. Lands ``Placeholder=T`` and ``Hidden=T`` into the
    placeholder + is_active flags. Non-currency holdings get a warning
    and land in ``--default-currency``.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"could not read {path}: {exc}") from exc

    try:
        parsed = gnucash_parse(text, default_currency=default_currency)
    except GnuCashParseError as exc:
        raise typer.BadParameter(f"GnuCash CSV parse failed: {exc}") from exc

    sorted_accounts = sort_by_depth(parsed)

    type_counts: dict[str, int] = {}
    for a in sorted_accounts:
        type_counts[a.type] = type_counts.get(a.type, 0) + 1
    warning_count = sum(1 for a in sorted_accounts if a.warning is not None)

    if dry_run:
        if as_json:
            sys.stdout.write(
                json.dumps(
                    {
                        "dry_run": True,
                        "row_count": len(sorted_accounts),
                        "by_type": type_counts,
                        "warning_count": warning_count,
                        "warnings": [
                            {
                                "name": a.name,
                                "full_path": a.full_path,
                                "warning": a.warning,
                                "symbol": a.raw_symbol,
                                "namespace": a.raw_namespace,
                            }
                            for a in sorted_accounts
                            if a.warning is not None
                        ],
                    }
                )
                + "\n"
            )
            return
        typer.echo(f"GnuCash CSV at {path}")
        typer.echo(f"  rows:     {len(sorted_accounts)}")
        for tulip_type, count in sorted(type_counts.items()):
            typer.echo(f"    {tulip_type}: {count}")
        if warning_count:
            typer.echo(f"  warnings: {warning_count}")
            for a in sorted_accounts:
                if a.warning is None:
                    continue
                typer.echo(
                    f"    [{a.warning}] {a.full_path} "
                    f"(Symbol={a.raw_symbol!r}, Namespace={a.raw_namespace!r}) "
                    f"→ landing in {default_currency}"
                )
        typer.echo("Run again without --dry-run to apply.")
        return

    # Live run. Walk the sorted list, POSTing each row.
    created = 0
    skipped = 0
    failed_paths: list[str] = []
    # full_path → account id, populated as we create rows so the next
    # child can resolve its parent without a round-trip.
    by_path: dict[str, str] = {}

    try:
        with _client(config, as_json=as_json) as client:
            # Seed the lookup with any existing accounts (idempotent re-run).
            existing = client.get("/v1/accounts", authenticated=True).json()
            for row in existing:
                full_path = _build_full_path_for_existing(row, existing)
                if full_path:
                    by_path[full_path] = str(row["id"])

            for account in sorted_accounts:
                if account.full_path in by_path:
                    skipped += 1
                    continue
                parent_path = ":".join(account.full_path.split(":")[:-1])
                parent_id = by_path.get(parent_path) if parent_path else None
                body: dict[str, Any] = {
                    "name": account.name,
                    "type": account.type,
                    "currency": account.currency,
                    "visibility": "shared",
                }
                if account.code:
                    body["code"] = account.code
                if account.subtype:
                    body["subtype"] = account.subtype
                # GnuCash Hidden=T gets stashed in notes rather than
                # immediately deactivating — deactivated accounts disappear
                # from GET /v1/accounts, which would break idempotent
                # re-run (the lookup couldn't find them). The operator
                # can deactivate manually with `tulip accounts deactivate`.
                notes = account.notes
                if not account.is_active:
                    hidden_note = "marked Hidden in GnuCash export"
                    notes = f"{notes}\n{hidden_note}" if notes else hidden_note
                if notes:
                    body["notes"] = notes
                if account.is_placeholder:
                    body["is_placeholder"] = True
                if parent_id:
                    body["parent_account_id"] = parent_id
                try:
                    resp = client.post("/v1/accounts", json=body, authenticated=True)
                except CliError as err:
                    failed_paths.append(f"{account.full_path}: {err}")
                    continue
                row = resp.json()
                by_path[account.full_path] = str(row["id"])
                created += 1
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(
            json.dumps(
                {
                    "created": created,
                    "skipped": skipped,
                    "failed": failed_paths,
                    "warning_count": warning_count,
                }
            )
            + "\n"
        )
        return
    typer.echo(
        f"GnuCash import complete: {created} created · {skipped} skipped · "
        f"{len(failed_paths)} failed"
    )
    if warning_count:
        typer.echo(
            f"  {warning_count} non-currency holding(s) landed in "
            f"{default_currency}; original symbol stashed in notes"
        )
    for failure in failed_paths:
        typer.echo(f"  FAIL {failure}", err=True)


def _build_full_path_for_existing(
    row: dict[str, Any], all_accounts: list[dict[str, Any]]
) -> str | None:
    """Reconstruct the GnuCash-style full_path for an existing Tulip account.

    Walks ``parent_account_id`` up to the root, prepending each name.
    Used by ``import-gnucash`` to seed its full_path → id lookup so
    re-runs are idempotent. Returns ``None`` if the chain references
    a parent that isn't in the listing (shouldn't happen, but bail
    rather than infinite-loop).
    """
    by_id = {str(a["id"]): a for a in all_accounts}
    names: list[str] = []
    seen: set[str] = set()
    cur: dict[str, Any] | None = row
    while cur is not None:
        cur_id = str(cur["id"])
        if cur_id in seen:
            return None
        seen.add(cur_id)
        names.append(str(cur.get("name", "")))
        parent_id = cur.get("parent_account_id")
        if parent_id is None:
            break
        cur = by_id.get(str(parent_id))
        if cur is None:
            return None
    names.reverse()
    return ":".join(names) if names else None
