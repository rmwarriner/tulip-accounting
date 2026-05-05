"""``tulip imports profiles`` — CRUD + YAML round-trip for CSV profiles (P5.2.c)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

csv_profiles_app = typer.Typer(
    name="profiles",
    help="Manage per-household CSV column-mapping profiles.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_profile_summary(p: dict[str, Any]) -> None:
    typer.echo(f"name:        {p.get('name', '')}")
    typer.echo(f"id:          {p.get('id', '')}")
    typer.echo(f"date column: {p.get('date_column', '')}")
    typer.echo(f"date format: {p.get('date_format', '')}")
    typer.echo(f"amount col:  {p.get('amount_column', '')}")
    typer.echo(f"amount sign: {p.get('amount_negative_means', '')}")
    typer.echo(f"desc column: {p.get('description_column', '')}")
    if p.get("reference_column"):
        typer.echo(f"ref column:  {p['reference_column']}")
    if p.get("counterparty_column"):
        typer.echo(f"cp  column:  {p['counterparty_column']}")
    typer.echo(f"encoding:    {p.get('encoding', '')}")
    typer.echo(f"delimiter:   {p.get('delimiter', '')!r}")
    typer.echo(f"skip rows:   {p.get('skip_header_rows', '')}")


def _render_profile_table(rows: list[dict[str, Any]]) -> None:
    table = Table(show_header=True, show_lines=False)
    table.add_column("name")
    table.add_column("date format")
    table.add_column("amount sign")
    table.add_column("delimiter")
    table.add_column("id")
    for row in rows:
        table.add_row(
            row.get("name", ""),
            row.get("date_format", ""),
            row.get("amount_negative_means", ""),
            repr(row.get("delimiter", "")),
            str(row.get("id", ""))[:8],
        )
    Console().print(table)


@csv_profiles_app.command("add")
def add_profile(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Profile name (unique per household)."),
    ] = None,
    date_column: Annotated[str | None, typer.Option("--date-column")] = None,
    date_format: Annotated[
        str | None,
        typer.Option("--date-format", help="strftime format, e.g. %m/%d/%Y."),
    ] = None,
    amount_column: Annotated[str | None, typer.Option("--amount-column")] = None,
    description_column: Annotated[str | None, typer.Option("--description-column")] = None,
    amount_negative_means: Annotated[
        str, typer.Option("--amount-negative-means", help="'debit' (default) or 'credit'.")
    ] = "debit",
    reference_column: Annotated[str | None, typer.Option("--reference-column")] = None,
    counterparty_column: Annotated[str | None, typer.Option("--counterparty-column")] = None,
    encoding: Annotated[str, typer.Option("--encoding")] = "utf-8",
    delimiter: Annotated[str, typer.Option("--delimiter")] = ",",
    skip_header_rows: Annotated[int, typer.Option("--skip-header-rows", min=0)] = 1,
    from_yaml: Annotated[
        Path | None,
        typer.Option(
            "--from-yaml",
            help="Load profile from a YAML file (mutually exclusive with --name etc.).",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Create a new CSV profile."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if from_yaml is not None:
        # Delegate to the import endpoint — round-trip with `export`.
        try:
            with _client(config, as_json=as_json) as client:
                response = _post_yaml_body(client, from_yaml.read_bytes())
        except CliError as err:
            err.render()
            raise typer.Exit(err.exit_code) from None
    else:
        if not (name and date_column and date_format and amount_column and description_column):
            raise typer.BadParameter(
                "either --from-yaml FILE or all of "
                "(--name, --date-column, --date-format, --amount-column, "
                "--description-column) must be provided"
            )
        body: dict[str, Any] = {
            "name": name,
            "date_column": date_column,
            "date_format": date_format,
            "amount_column": amount_column,
            "description_column": description_column,
            "amount_negative_means": amount_negative_means,
            "encoding": encoding,
            "delimiter": delimiter,
            "skip_header_rows": skip_header_rows,
        }
        if reference_column:
            body["reference_column"] = reference_column
        if counterparty_column:
            body["counterparty_column"] = counterparty_column
        try:
            with _client(config, as_json=as_json) as client:
                response = client.post("/v1/imports/profiles", json=body, authenticated=True)
        except CliError as err:
            err.render()
            raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"Created CSV profile {response.json()['name']!r}.")


def _post_yaml_body(client: TulipClient, yaml_bytes: bytes) -> httpx.Response:
    """Helper: POST raw YAML bytes to /v1/imports/profiles/import."""
    return client.post_raw(
        "/v1/imports/profiles/import",
        body=yaml_bytes,
        content_type="application/x-yaml",
        authenticated=True,
    )


@csv_profiles_app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    """List CSV profiles in the household."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/imports/profiles", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    rows = response.json()
    if not rows:
        typer.echo("No CSV profiles. Create one with 'tulip imports profiles add ...'.")
        return
    _render_profile_table(rows)


@csv_profiles_app.command("show")
def show_profile(
    ctx: typer.Context,
    name_or_id: Annotated[str, typer.Argument(metavar="NAME_OR_ID")],
) -> None:
    """Show one CSV profile by UUID or name."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(f"/v1/imports/profiles/{name_or_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_profile_summary(response.json())


@csv_profiles_app.command("delete")
def delete_profile(
    ctx: typer.Context,
    name_or_id: Annotated[str, typer.Argument(metavar="NAME_OR_ID")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a CSV profile by UUID or name."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    if not yes:
        if not typer.confirm(f"Delete CSV profile {name_or_id!r}?", default=False):
            typer.echo("Aborted; no changes made.")
            return
    try:
        with _client(config, as_json=as_json) as client:
            client.delete(f"/v1/imports/profiles/{name_or_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write('{"deleted": "' + name_or_id + '"}\n')
        return
    typer.echo(f"Deleted CSV profile {name_or_id!r}.")


@csv_profiles_app.command("export")
def export_profile(
    ctx: typer.Context,
    name_or_id: Annotated[str, typer.Argument(metavar="NAME_OR_ID")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write YAML to this path (default: stdout)."),
    ] = None,
) -> None:
    """Export a CSV profile as YAML."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(
                f"/v1/imports/profiles/{name_or_id}/export",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if out is not None:
        out.write_text(response.text)
        if not as_json:
            typer.echo(f"Wrote profile YAML to {out}")
    else:
        sys.stdout.write(response.text)
        if not response.text.endswith("\n"):
            sys.stdout.write("\n")


@csv_profiles_app.command("import")
def import_profile(
    ctx: typer.Context,
    yaml_path: Annotated[
        Path,
        typer.Argument(
            metavar="FILE",
            help="YAML file emitted by 'tulip imports profiles export'.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
) -> None:
    """Import a CSV profile from a YAML file."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = _post_yaml_body(client, yaml_path.read_bytes())
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"Imported CSV profile {response.json()['name']!r}.")
