"""``tulip import ofx`` — upload a statement file to /v1/imports.

Pure CLI surface over the imports API endpoint. Reads the file from
disk, resolves ``--account`` to a UUID via the shared resolver, and
issues a multipart POST through ``TulipClient.post_multipart``.

Per ADR-0004 §"Module layout", the importer's parsing logic lives in
``tulip_importers.ofx`` and the CLI never invokes it directly — the
API does. This keeps the CLI a pure network client (architecture
test in ``tulip-cli/tests/test_architecture.py`` enforces this).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from tulip_cli._picker import is_interactive, pick
from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.commands.csv_profiles import csv_profiles_app
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_USER, CliError
from tulip_cli.http import TulipClient

imports_app = typer.Typer(
    name="imports",
    help="Upload statement files (OFX, QIF, CSV) and manage CSV profiles.",
    no_args_is_help=True,
)
imports_app.add_typer(csv_profiles_app, name="profiles")


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_summary(body: dict[str, Any]) -> None:
    typer.echo(
        f"Imported {body.get('statement_line_count', 0)} statement lines "
        f"into batch {body.get('id', '')} "
        f"({body.get('source_format', 'ofx')} from {body.get('source_filename', '')})."
    )


def _do_import(
    ctx: typer.Context,
    *,
    file_path: Path,
    account: str,
    source_format: str,
    content_type: str,
    extra_form: dict[str, str] | None = None,
    apply: bool = False,
    no_categorize: bool = False,
    posted: bool = False,
) -> None:
    """Shared upload flow: resolve account, read file, multipart POST.

    ``extra_form`` carries format-specific form fields (e.g.,
    ``profile_id`` for CSV uploads) merged with the standard
    ``account_id``/``source_format`` pair.

    When ``apply=True`` (#299), follow the parse-POST with a second
    call to ``/v1/imports/{batch_id}/apply``. The two calls are
    orchestrated client-side; if the apply fails, the batch is left
    in PARSED state and recoverable via standalone ``tulip imports
    apply <BATCH_ID>``. ``no_categorize`` and ``posted`` compose with
    ``apply`` to mirror the standalone apply command's flags.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            account_record = _resolve_account(client, account)
            account_id = str(account_record["id"])
            raw_bytes = file_path.read_bytes()
            data: dict[str, str] = {
                "account_id": account_id,
                "source_format": source_format,
            }
            if extra_form:
                data.update(extra_form)
            response = client.post_multipart(
                "/v1/imports",
                files={"file": (file_path.name, raw_bytes, content_type)},
                data=data,
                authenticated=True,
            )
            import_body = response.json()
            if apply:
                # The /v1/imports response carries the new batch UUID as
                # ``id`` (ImportBatchSummary schema), not ``batch_id``.
                batch_id = str(import_body["id"])
                apply_response = _apply_call(
                    client,
                    batch_id=batch_id,
                    no_categorize=no_categorize,
                    posted=posted,
                )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        if apply:
            # Combined envelope so callers can dispatch on both halves.
            combined = {
                "imported": import_body,
                "applied": apply_response.json(),
            }
            sys.stdout.write(json.dumps(combined) + "\n")
        else:
            sys.stdout.write(response.text + "\n")
        return
    _render_summary(import_body)
    if apply:
        apply_body = apply_response.json()
        landed_as = "POSTED" if posted else "PENDING"
        typer.echo(
            f"Applied batch {apply_body['batch_id']}: created "
            f"{apply_body['created_count']} {landed_as} transactions, "
            f"skipped {apply_body['skipped_count']} lines."
        )


def _apply_call(
    client: TulipClient,
    *,
    batch_id: str,
    no_categorize: bool,
    posted: bool,
) -> httpx.Response:
    """Issue the apply call with the same query-string shape as `tulip imports apply`."""
    path = f"/v1/imports/{batch_id}/apply"
    query: list[str] = []
    if no_categorize:
        query.append("no_categorize=true")
    if posted:
        query.append("as_posted=true")
    if query:
        path += "?" + "&".join(query)
    return client.post(path, authenticated=True)


def _resolve_profile_id(client: TulipClient, profile: str) -> str:
    """Resolve a CSV profile name (or UUID) to a UUID via the API."""
    response = client.get(f"/v1/imports/profiles/{profile}", authenticated=True)
    return str(response.json()["id"])


@imports_app.command("ofx")
def import_ofx(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to an OFX 1.x SGML or 2.x XML statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to. UUID or code (resolved "
                "the same way as `accounts show`)."
            ),
        ),
    ],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "After parsing, immediately apply the batch — every "
                "non-excluded line becomes a ledger transaction in one "
                "command (#299). Composes with --no-categorize and --posted."
            ),
        ),
    ] = False,
    no_categorize: Annotated[
        bool,
        typer.Option(
            "--no-categorize",
            help="With --apply: skip the AI categorizer (see `tulip imports apply --help`).",
        ),
    ] = False,
    posted: Annotated[
        bool,
        typer.Option(
            "--posted",
            help="With --apply: land lines as POSTED instead of PENDING.",
        ),
    ] = False,
) -> None:
    """Upload an OFX file; the API parses it and persists a batch."""
    _do_import(
        ctx,
        file_path=file_path,
        account=account,
        source_format="ofx",
        content_type="application/x-ofx",
        apply=apply,
        no_categorize=no_categorize,
        posted=posted,
    )


@imports_app.command("csv")
def import_csv(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a CSV statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to. UUID or code. The "
                "account's currency is applied to every line."
            ),
        ),
    ],
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            help=(
                "CSV column-mapping profile (UUID or name). Resolved client-side before the upload."
            ),
        ),
    ],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "After parsing, immediately apply the batch (#299). "
                "Composes with --no-categorize and --posted."
            ),
        ),
    ] = False,
    no_categorize: Annotated[
        bool,
        typer.Option(
            "--no-categorize",
            help="With --apply: skip the AI categorizer.",
        ),
    ] = False,
    posted: Annotated[
        bool,
        typer.Option(
            "--posted",
            help="With --apply: land lines as POSTED instead of PENDING.",
        ),
    ] = False,
) -> None:
    """Upload a CSV file with the named profile; the API parses it."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            profile_id = _resolve_profile_id(client, profile)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    _do_import(
        ctx,
        file_path=file_path,
        account=account,
        source_format="csv",
        content_type="text/csv",
        extra_form={"profile_id": profile_id},
        apply=apply,
        no_categorize=no_categorize,
        posted=posted,
    )


_VALID_LIST_STATUSES = ("parsed", "applied", "reverted")


@imports_app.command("list")
def list_imports(
    ctx: typer.Context,
    status_: Annotated[
        str | None,
        typer.Option(
            "--status",
            help="Filter by batch status. One of: parsed, applied, reverted.",
        ),
    ] = None,
    account: Annotated[
        str | None,
        typer.Option(
            "--account",
            help=(
                "Filter to batches uploaded against this account. UUID or code "
                "(resolved the same way as `accounts show`)."
            ),
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="Cap on rows returned (1-200). Defaults to 25.",
            min=1,
            max=200,
        ),
    ] = None,
) -> None:
    """List recent import batches, newest first.

    Use the printed ID prefix (first 8 chars) with ``tulip imports show
    <prefix>`` to drill into a batch.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if status_ is not None and status_ not in _VALID_LIST_STATUSES:
        raise typer.BadParameter(
            f"--status must be one of {', '.join(_VALID_LIST_STATUSES)} (got {status_!r})"
        )

    params: dict[str, str] = {}
    try:
        with _client(config, as_json=as_json) as client:
            if account is not None:
                resolved = _resolve_account(client, account)
                params["account_id"] = str(resolved["id"])
            if status_ is not None:
                params["status"] = status_
            if limit is not None:
                params["limit"] = str(limit)
            response = client.get("/v1/imports", authenticated=True, params=params)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    items = body.get("items") or []
    if not items:
        typer.echo("No import batches match.")
        return
    _render_list_table(items)
    if body.get("next_cursor"):
        typer.echo(
            "\nMore batches available. Re-run with --limit to widen the page, or filter further."
        )


def _render_list_table(items: list[dict[str, Any]]) -> None:
    """Render a list of ``ImportBatchListItem`` dicts as a Rich table."""
    from rich.table import Table

    from tulip_cli._console import make_console
    from tulip_cli._tables import add_numeric_column

    table = Table(show_header=True, show_lines=False)
    table.add_column("id")
    table.add_column("created")
    table.add_column("status")
    table.add_column("format")
    table.add_column("account")
    table.add_column("filename")
    add_numeric_column(table, "counts")
    for item in items:
        batch_id = str(item.get("id") or "")
        account_id = str(item.get("account_id") or "")
        created = str(item.get("created_at") or "")
        # ISO-8601 timestamps are 19+ chars; trim microseconds + timezone for
        # readability while keeping date + time-of-day.
        if len(created) >= 19:
            created = created[:19].replace("T", " ")
        table.add_row(
            batch_id[:8] if batch_id else "—",
            created,
            str(item.get("status") or ""),
            str(item.get("source_format") or "").upper(),
            account_id[:8] if account_id else "—",
            str(item.get("source_filename") or ""),
            f"{item.get('imported_count', 0)}/{item.get('skipped_count', 0)}",
        )
    make_console().print(table)


@imports_app.command("show")
def show_import(
    ctx: typer.Context,
    batch_id: Annotated[
        str,
        typer.Argument(
            help="Import batch UUID returned by `tulip imports ofx/qif/csv`.",
            metavar="BATCH_ID",
        ),
    ],
) -> None:
    """Render an import batch's header + parsed statement lines."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(f"/v1/imports/{batch_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_batch(response.json())


def _render_batch(body: dict[str, Any]) -> None:
    """Render an ``ImportBatchRead`` body to stdout."""
    from rich.table import Table

    from tulip_cli._console import make_console
    from tulip_cli._tables import add_numeric_column

    console = make_console()
    header_lines = [
        f"Batch:    {body.get('id', '')}",
        f"Source:   {body.get('source_filename', '')} ({body.get('source_format', '?').upper()})",
        f"Account:  {body.get('account_id', '')}",
        f"Status:   {body.get('status', '?')}",
        f"Counts:   imported={body.get('imported_count', 0)}  "
        f"skipped={body.get('skipped_count', 0)}  "
        f"errors={body.get('error_count', 0)}",
        f"Created:  {body.get('created_at', '')}",
    ]
    applied_at = body.get("applied_at")
    if applied_at:
        header_lines.append(f"Applied:  {applied_at}")
    reverted_at = body.get("reverted_at")
    if reverted_at:
        header_lines.append(f"Reverted: {reverted_at}")
    for line in header_lines:
        typer.echo(line)

    lines = body.get("lines") or []
    if not lines:
        typer.echo("\n(no statement lines)")
        return

    table = Table(title=f"\nStatement lines ({len(lines)})", show_header=True)
    add_numeric_column(table, "#")
    table.add_column("date")
    add_numeric_column(table, "amount")
    table.add_column("ccy")
    table.add_column("description")
    table.add_column("flag")
    from tulip_cli._money_format import format_amount

    for line in lines:
        flag_bits: list[str] = []
        if line.get("is_excluded"):
            flag_bits.append("excluded")
        if line.get("reconciliation_match_id"):
            flag_bits.append("reconciled")
        currency = str(line.get("currency", ""))
        table.add_row(
            str(line.get("line_number", "")),
            str(line.get("posted_date", "")),
            format_amount(line.get("amount"), currency),
            currency,
            str(line.get("description", "") or ""),
            ", ".join(flag_bits),
        )
    console.print(table)


def _format_apply_picker_label(item: dict[str, Any]) -> str:
    """One-line label for an actionable (status=parsed) batch in the picker."""
    batch_id = str(item.get("id") or "")
    created = str(item.get("created_at") or "")
    if len(created) >= 19:
        created = created[:19].replace("T", " ")
    fmt = str(item.get("source_format") or "").upper()
    filename = str(item.get("source_filename") or "")
    imported = item.get("imported_count", 0)
    skipped = item.get("skipped_count", 0)
    return (
        f"{batch_id[:8] if batch_id else '—'}  {created}  {fmt:>4}  "
        f"{filename}  ({imported}/{skipped})"
    )


def _pick_apply_batch_id(config: Config, *, as_json: bool) -> str | None:
    """Fetch actionable (parsed) batches and prompt the user to pick one.

    Returns the picked UUID string, or ``None`` if the picker is
    suppressed (no TTY, ``--json``) or the user cancels. Non-interactive
    callers get a usage hint on stderr matching the legacy "missing
    argument" message so scripts can ``grep`` it.
    """
    if as_json or not is_interactive():
        typer.echo(
            "Missing argument BATCH_ID. Run `tulip imports list --status parsed` "
            "to find a batch, then re-run with the id.",
            err=True,
        )
        return None
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(
                "/v1/imports",
                authenticated=True,
                params={"status": "parsed"},
            )
    except CliError as err:
        err.render()
        return None
    items = response.json().get("items") or []
    return pick(
        items,
        label=_format_apply_picker_label,
        title="Pick a parsed import batch to apply:",
        empty_message=(
            "No parsed import batches to apply. Upload one with `tulip imports ofx/qif/csv` first."
        ),
        overflow_hint=("  …list truncated; narrow with `tulip imports list --account <id>`."),
    )


@imports_app.command("apply")
def apply_import(
    ctx: typer.Context,
    batch_id: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Import batch UUID returned by `tulip imports ofx/qif/csv`. "
                "Omit to pick interactively from recent parsed batches "
                "(TTY only — scripts still get the usage error)."
            ),
            metavar="BATCH_ID",
        ),
    ] = None,
    no_categorize: Annotated[
        bool,
        typer.Option(
            "--no-categorize",
            help=(
                "Skip the AI categorizer; route every line to the "
                "household's Imbalance:Unknown account (auto-created per "
                "currency on first use). Useful for bulk migrations from "
                "another tool where you'll assign categories manually."
            ),
        ),
    ] = False,
    posted: Annotated[
        bool,
        typer.Option(
            "--posted",
            help=(
                "Land every promoted line as POSTED instead of PENDING "
                "(skips the review step). Each line lands committed; "
                "use `tulip transactions edit` to fix categorizations "
                "later. Useful when every imported line is already "
                "cleared by the bank (migration workflows from "
                "Banktivity, Quicken, GnuCash, etc.)."
            ),
        ),
    ] = False,
    treat_cleared_as_pending: Annotated[
        bool,
        typer.Option(
            "--treat-cleared-as-pending",
            help=(
                "Force every line to PENDING even when the source format "
                "(e.g. QIF C field, #279) marks it cleared or reconciled. "
                "Legacy 'everything pending' behaviour for users who want "
                "the manual review pass."
            ),
        ),
    ] = False,
) -> None:
    """Apply a parsed batch: every non-excluded line becomes a ledger transaction.

    By default, new transactions are PENDING (review queue). Pass
    ``--posted`` to land them as POSTED directly — useful for migrations
    where every line is already cleared by the source bank/tool.

    For QIF imports (#279), the ``C`` (cleared) field is consulted by
    default: ``c``/``*`` lands as POSTED, ``R`` as RECONCILED, empty
    as PENDING. Pass ``--treat-cleared-as-pending`` to ignore the hint
    and put every line in the review queue.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    if batch_id is None:
        batch_id = _pick_apply_batch_id(config, as_json=as_json)
        if batch_id is None:
            raise typer.Exit(2)
    path = f"/v1/imports/{batch_id}/apply"
    query: list[str] = []
    if no_categorize:
        query.append("no_categorize=true")
    if posted:
        query.append("as_posted=true")
    if treat_cleared_as_pending:
        query.append("treat_cleared_as_pending=true")
    if query:
        path += "?" + "&".join(query)
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(path, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    landed_as = "POSTED" if posted else "PENDING"
    typer.echo(
        f"Applied batch {body['batch_id']}: created {body['created_count']} "
        f"{landed_as} transactions, skipped {body['skipped_count']} lines."
    )


def _load_account_map(path: Path) -> dict[str, str]:
    """Load + validate a JSON account-map file: ``{qif name: account id/code}``.

    The map routes each ``!Account`` block in a multi-account QIF to a
    tulip account. Values are resolved with the same UUID/code/name/path
    resolver as ``--account`` (#197). JSON only for now — a YAML reader
    would mean a new CLI dependency.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"--account-map {path} is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise typer.BadParameter(
            f"--account-map {path} must be a non-empty JSON object "
            '({"QIF account name": "tulip account code or UUID"}).'
        )
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise typer.BadParameter(
                f"--account-map {path}: every entry must map a QIF account "
                "name (string) to a non-empty account code / UUID (string)."
            )
        out[key] = value.strip()
    return out


def _render_starter_map(account_names: list[str]) -> None:
    """Print the copy-pasteable starter --account-map for a multi-account QIF."""
    typer.echo(
        f"Multi-account QIF — {len(account_names)} accounts found. "
        "Create a JSON account map and re-run with --account-map:\n",
        err=True,
    )
    starter = {name: "<tulip account code or UUID>" for name in account_names}
    typer.echo(json.dumps(starter, indent=2), err=True)


def _post_qif_single(
    client: TulipClient,
    file_path: Path,
    raw_bytes: bytes,
    *,
    account_id: str,
) -> dict[str, Any]:
    """POST a single-account QIF to /v1/imports; return the batch summary dict."""
    response = client.post_multipart(
        "/v1/imports",
        files={"file": (file_path.name, raw_bytes, "application/qif")},
        data={"account_id": account_id, "source_format": "qif"},
        authenticated=True,
    )
    return dict(response.json())


def _render_multi_account_summary(body: dict[str, Any], uuid_to_name: dict[str, str]) -> None:
    """Render a ``MultiAccountImportSummary``: per-account batches + transfers."""
    for batch in body.get("batches", []):
        account_id = str(batch.get("account_id", ""))
        name = uuid_to_name.get(account_id, account_id)
        typer.echo(f"[{name}] ", nl=False)
        _render_summary(batch)
    transfer_count = body.get("transfer_count", 0)
    if transfer_count:
        plural = "" if transfer_count == 1 else "s"
        typer.echo(
            f"Paired {transfer_count} cross-account transfer{plural} "
            "into balanced PENDING transactions."
        )
    for warning in body.get("warnings", []):
        typer.echo(f"  warning: {warning}", err=True)


@imports_app.command("qif")
def import_qif(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a QIF (Quicken Interchange Format) statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str | None,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to (single-account QIF). "
                "UUID or code. The account's currency is applied to every "
                "line — QIF doesn't carry currency in the file itself. "
                "Mutually exclusive with --account-map."
            ),
        ),
    ] = None,
    account_map: Annotated[
        Path | None,
        typer.Option(
            "--account-map",
            help=(
                "JSON file mapping each QIF !Account name to a tulip account "
                "code/UUID, for a multi-account QIF. Mutually exclusive with "
                "--account. Run with --account on a multi-account file to get "
                "a starter map."
            ),
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "After parsing, immediately apply the batch (#299). "
                "Single-account QIF only — multi-account (--account-map) "
                "produces N batches and apply-per-batch is left to the "
                "explicit `tulip imports apply` flow. Composes with "
                "--no-categorize and --posted."
            ),
        ),
    ] = False,
    no_categorize: Annotated[
        bool,
        typer.Option(
            "--no-categorize",
            help="With --apply: skip the AI categorizer.",
        ),
    ] = False,
    posted: Annotated[
        bool,
        typer.Option(
            "--posted",
            help="With --apply: land lines as POSTED instead of PENDING.",
        ),
    ] = False,
) -> None:
    """Upload a QIF file; the API parses it and persists a batch.

    Single-account QIF: pass ``--account``. Multi-account QIF (one file
    holding several ``!Account`` blocks): pass ``--account-map`` to route
    each account. Running ``--account`` against a multi-account file
    prints a copy-pasteable starter map.
    """
    if account is not None and account_map is not None:
        raise typer.BadParameter("pass --account OR --account-map, not both")
    if account is None and account_map is None:
        raise typer.BadParameter(
            "pass --account (single-account QIF) or --account-map (multi-account QIF)"
        )
    if apply and account_map is not None:
        raise typer.BadParameter(
            "--apply is single-account-QIF only; use `tulip imports apply <BATCH_ID>` "
            "per batch returned from a multi-account import"
        )

    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    raw_bytes = file_path.read_bytes()

    if account is not None:
        # Single-account path. Intercept the multi-account rejection so we
        # can render the friendly starter map instead of the raw error.
        try:
            with _client(config, as_json=as_json) as client:
                account_record = _resolve_account(client, account)
                summary = _post_qif_single(
                    client, file_path, raw_bytes, account_id=str(account_record["id"])
                )
                apply_summary: dict[str, Any] | None = None
                if apply:
                    apply_response = _apply_call(
                        client,
                        batch_id=str(summary["id"]),
                        no_categorize=no_categorize,
                        posted=posted,
                    )
                    apply_summary = dict(apply_response.json())
        except CliError as err:
            if err.problem.get("code") == "import.multi_account_qif" and not as_json:
                _render_starter_map(list(err.problem.get("account_names", [])))
                raise typer.Exit(EXIT_USER) from None
            err.render()
            raise typer.Exit(err.exit_code) from None
        if as_json:
            if apply_summary is not None:
                sys.stdout.write(json.dumps({"imported": summary, "applied": apply_summary}) + "\n")
            else:
                sys.stdout.write(json.dumps(summary) + "\n")
            return
        _render_summary(summary)
        if apply_summary is not None:
            landed_as = "POSTED" if posted else "PENDING"
            typer.echo(
                f"Applied batch {apply_summary['batch_id']}: created "
                f"{apply_summary['created_count']} {landed_as} transactions, "
                f"skipped {apply_summary['skipped_count']} lines."
            )
        return

    # Multi-account path: resolve every map entry up front (a bad code
    # fails before any batch lands), then POST the whole file + the
    # resolved map in one request. The server splits it by !Account,
    # creates a batch per account, and pairs cross-account transfers.
    assert account_map is not None  # noqa: S101 — guarded by the checks above
    name_to_identifier = _load_account_map(account_map)
    try:
        with _client(config, as_json=as_json) as client:
            resolved: dict[str, str] = {
                qif_name: str(_resolve_account(client, identifier)["id"])
                for qif_name, identifier in name_to_identifier.items()
            }
            response = client.post_multipart(
                "/v1/imports/multi-account",
                files={"file": (file_path.name, raw_bytes, "application/qif")},
                data={"account_map": json.dumps(resolved)},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    body = dict(response.json())
    if as_json:
        sys.stdout.write(json.dumps(body) + "\n")
        return
    _render_multi_account_summary(body, {v: k for k, v in resolved.items()})
