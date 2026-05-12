"""``tulip reports`` — wrappers over ``/v1/reports/*`` (P7.1.b).

One subcommand per report (9 total). Each subcommand forwards its
options to the matching query parameters on the report endpoint and
renders the response body to stdout or to ``--output PATH``.

Format selection:

* ``--format json`` (default): pretty pass-through of the JSON body
  (stdout) or written verbatim to ``--output``.
* ``--format html``: HTML rendering. Writes to stdout (terminal-safe)
  or ``--output``.
* ``--format pdf`` / ``--format csv``: binary / structured. ``--output``
  is required since piping these to a terminal isn't useful.

The CLI is a pure network client — all rendering happens server-side
in ``tulip_reports``. Architecture test
``tests/test_architecture.py`` keeps it that way.
"""

from __future__ import annotations

import sys
from datetime import date as date_type
from pathlib import Path
from typing import Annotated

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

reports_app = typer.Typer(
    name="reports",
    help="Fetch the 9 ledger reports (JSON / HTML / PDF / CSV).",
    no_args_is_help=True,
)


_BINARY_FORMATS = frozenset({"pdf", "csv"})


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _validate_date(value: str | None, flag: str) -> None:
    if value is None:
        return
    try:
        date_type.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{flag} must be YYYY-MM-DD") from exc


def _fetch_and_emit(
    ctx: typer.Context,
    *,
    endpoint: str,
    params: dict[str, str],
    fmt: str,
    output: Path | None,
) -> None:
    """Shared GET-render-write flow used by every report subcommand."""
    if fmt in _BINARY_FORMATS and output is None:
        raise typer.BadParameter(
            f"--format {fmt} requires --output PATH (binary output isn't terminal-safe)."
        )
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    request_params = dict(params)
    request_params["format"] = fmt
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(endpoint, authenticated=True, params=request_params)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if output is not None:
        output.write_bytes(response.content)
        if not as_json:
            typer.echo(f"Wrote {len(response.content)} bytes to {output}")
        return

    if fmt in _BINARY_FORMATS:  # pragma: no cover — guarded above
        raise RuntimeError("unreachable: binary formats require --output")
    sys.stdout.write(response.text)
    if not response.text.endswith("\n"):
        sys.stdout.write("\n")


# -----------------------------------------------------------------------
# trial-balance, balance-sheet, envelope-status, sinking-fund-progress
# share a single shape (``--as-of``-only).
# -----------------------------------------------------------------------


def _as_of_only(
    ctx: typer.Context,
    endpoint: str,
    *,
    as_of: str | None,
    fmt: str,
    output: Path | None,
) -> None:
    _validate_date(as_of, "--as-of")
    params: dict[str, str] = {}
    if as_of is not None:
        params["as_of"] = as_of
    _fetch_and_emit(ctx, endpoint=endpoint, params=params, fmt=fmt, output=output)


@reports_app.command("trial-balance")
def trial_balance(
    ctx: typer.Context,
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Point-in-time date (YYYY-MM-DD). Defaults to today."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", help="Response format: json|html|pdf|csv.", case_sensitive=False),
    ] = "json",
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write the response body to this file instead of stdout."),
    ] = None,
) -> None:
    """Per-account balances + per-currency debit/credit totals."""
    _as_of_only(ctx, "/v1/reports/trial-balance", as_of=as_of, fmt=fmt, output=output)


@reports_app.command("balance-sheet")
def balance_sheet(
    ctx: typer.Context,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Assets / liabilities / equity snapshot at ``--as-of``."""
    _as_of_only(ctx, "/v1/reports/balance-sheet", as_of=as_of, fmt=fmt, output=output)


@reports_app.command("envelope-status")
def envelope_status(
    ctx: typer.Context,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Active envelopes: balance vs budget at ``--as-of``."""
    _as_of_only(ctx, "/v1/reports/envelope-status", as_of=as_of, fmt=fmt, output=output)


@reports_app.command("sinking-fund-progress")
def sinking_fund_progress(
    ctx: typer.Context,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Active sinking funds: balance vs target at ``--as-of``."""
    _as_of_only(ctx, "/v1/reports/sinking-fund-progress", as_of=as_of, fmt=fmt, output=output)


# -----------------------------------------------------------------------
# income-statement + cash-flow take period boundaries.
# -----------------------------------------------------------------------


@reports_app.command("income-statement")
def income_statement(
    ctx: typer.Context,
    start: Annotated[str, typer.Option("--start", help="Period start (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Period end (YYYY-MM-DD).")],
    prior_start: Annotated[
        str | None,
        typer.Option("--prior-start", help="Optional comparison period start (YYYY-MM-DD)."),
    ] = None,
    prior_end: Annotated[
        str | None,
        typer.Option("--prior-end", help="Optional comparison period end (YYYY-MM-DD)."),
    ] = None,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Revenue, expenses, and net income over a period."""
    for value, flag in (
        (start, "--start"),
        (end, "--end"),
        (prior_start, "--prior-start"),
        (prior_end, "--prior-end"),
    ):
        _validate_date(value, flag)
    params: dict[str, str] = {"start": start, "end": end}
    if prior_start is not None:
        params["prior_start"] = prior_start
    if prior_end is not None:
        params["prior_end"] = prior_end
    _fetch_and_emit(
        ctx, endpoint="/v1/reports/income-statement", params=params, fmt=fmt, output=output
    )


@reports_app.command("cash-flow")
def cash_flow(
    ctx: typer.Context,
    start: Annotated[str, typer.Option("--start")],
    end: Annotated[str, typer.Option("--end")],
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Net change per asset account over a period."""
    _validate_date(start, "--start")
    _validate_date(end, "--end")
    _fetch_and_emit(
        ctx,
        endpoint="/v1/reports/cash-flow",
        params={"start": start, "end": end},
        fmt=fmt,
        output=output,
    )


# -----------------------------------------------------------------------
# reconciliation-summary, audit-log, custom-query: bespoke filters.
# -----------------------------------------------------------------------


@reports_app.command("reconciliation-summary")
def reconciliation_summary(
    ctx: typer.Context,
    status: Annotated[
        str | None,
        typer.Option("--status", help="Optional status filter (e.g. open, complete)."),
    ] = None,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Reconciliations newest-first; filter by status."""
    params: dict[str, str] = {}
    if status is not None:
        params["status"] = status
    _fetch_and_emit(
        ctx, endpoint="/v1/reports/reconciliation-summary", params=params, fmt=fmt, output=output
    )


@reports_app.command("audit-log")
def audit_log(
    ctx: typer.Context,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    actor: Annotated[
        str | None,
        typer.Option("--actor", help="Filter by actor user UUID."),
    ] = None,
    entity_type: Annotated[
        str | None,
        typer.Option("--entity-type", help="Filter by entity type (e.g. transaction, account)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Filtered, paginated audit-log entries."""
    _validate_date(start, "--start")
    _validate_date(end, "--end")
    params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    if actor is not None:
        params["actor_user_id"] = actor
    if entity_type is not None:
        params["entity_type"] = entity_type
    _fetch_and_emit(ctx, endpoint="/v1/reports/audit-log", params=params, fmt=fmt, output=output)


@reports_app.command("custom-query")
def custom_query(
    ctx: typer.Context,
    sql: Annotated[
        str,
        typer.Option(
            "--sql",
            help=(
                "Read-only SELECT against the AI views. Subject to the same "
                "SQL-safety gate as ``tulip ai query``."
            ),
        ),
    ],
    fmt: Annotated[str, typer.Option("--format", case_sensitive=False)] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Render an ad-hoc SELECT as a tabular report."""
    _fetch_and_emit(
        ctx, endpoint="/v1/reports/custom-query", params={"sql": sql}, fmt=fmt, output=output
    )


__all__ = ["reports_app"]
