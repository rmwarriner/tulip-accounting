"""``tulip admin`` — operator surfaces for audit-log retention (#245).

Two command groups:

* ``tulip admin audit-policy show / set <tier> <days>`` — read + edit
  the household's per-tier audit retention overrides.
* ``tulip admin audit-prune`` — synchronously trigger the prune handler
  for the caller's household (ops debugging; the daily scheduled
  handler runs across every household automatically).
"""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

admin_app = typer.Typer(
    name="admin",
    help="Administrative operations: audit-log retention policy + manual prune.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


audit_policy_app = typer.Typer(
    name="audit-policy",
    help="Read or edit the household's audit-log retention tiers (admin-only).",
    no_args_is_help=True,
)
admin_app.add_typer(audit_policy_app)


_TIER_KEYS = ("ledger_days", "auth_days", "ai_days", "admin_days", "default_days")


@audit_policy_app.command("show")
def audit_policy_show(ctx: typer.Context) -> None:
    """Print the resolved per-tier retention (overrides merged with defaults)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/admin/audit-policy", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo("Audit-log retention policy (resolved):")
    for tier in _TIER_KEYS:
        days = body.get(tier)
        # Print a rough year-equivalent for the ledger tier so the
        # operator can sanity-check it against tax-record requirements.
        suffix = ""
        if tier == "ledger_days" and isinstance(days, int):
            suffix = f"  (~{days / 365:.1f} years)"
        typer.echo(f"  {tier:14s} = {days}{suffix}")
    typer.echo(
        "Defaults: 2555d ledger / 90d auth / 30d AI / 365d admin / 90d default. "
        "Override via `tulip admin audit-policy set <tier> <days>`."
    )


@audit_policy_app.command("set")
def audit_policy_set(
    ctx: typer.Context,
    tier: Annotated[
        str,
        typer.Argument(
            help="One of ledger_days / auth_days / ai_days / admin_days / default_days.",
        ),
    ],
    days: Annotated[
        int,
        typer.Argument(help="Positive integer; rows older than this many days get pruned."),
    ],
) -> None:
    """Override one tier's retention day-count."""
    if tier not in _TIER_KEYS:
        typer.echo(
            f"tier must be one of {', '.join(_TIER_KEYS)}, got {tier!r}",
            err=True,
        )
        raise typer.Exit(1)
    if days <= 0:
        typer.echo("days must be a positive integer", err=True)
        raise typer.Exit(1)
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.put(
                "/v1/admin/audit-policy",
                json={tier: days},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(f"audit-policy: {tier} = {body.get(tier)}")


@admin_app.command("audit-prune")
def audit_prune(ctx: typer.Context) -> None:
    """Synchronously prune the caller's household's audit_log (#245).

    The daily scheduled handler runs across every household
    automatically; this is the manual trigger for ops debugging.
    Returns per-tier deletion counts.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post("/v1/admin/audit-prune", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    per_tier = body.get("deleted_per_tier") or {}
    total = body.get("total_deleted", 0)
    typer.echo(f"audit-prune: {total} row(s) deleted")
    for tier in _TIER_KEYS:
        count = per_tier.get(tier, 0)
        typer.echo(f"  {tier:14s} {count}")
    # JSON also available; print as a hint for scripted users.
    if total > 0 and not as_json:
        typer.echo(f"  (raw summary: {json.dumps(per_tier, ensure_ascii=False)})", err=True)


__all__ = ["admin_app"]
