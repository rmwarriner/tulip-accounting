"""``tulip ai`` — BYOK key management, status, preview (P6.1, ADR-0005).

Sub-commands:

* ``set-key --provider X``     — upload an API key (interactive prompt or ``--key-stdin``).
* ``forget-key --provider X``  — remove the household's key for ``X``.
* ``list-keys``                — providers that have keys configured.
* ``status``                   — resolved policy summary.
* ``preview``                  — byte-faithful redacted prompt body.

All commands talk to ``/v1/ai/...`` endpoints; no direct DB access from
the CLI. Keys are read via ``getpass`` (interactive) or stdin (scripted).
"""

from __future__ import annotations

import getpass
import json
import sys
from datetime import date
from decimal import Decimal
from typing import Annotated

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

ai_app = typer.Typer(
    name="ai",
    help="BYOK key management, policy status, and prompt preview.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _read_key_from_stdin() -> str:
    if sys.stdin.isatty():
        typer.echo(
            "Enter API key (input is visible; omit --key-stdin to hide):",
            err=True,
        )
    return sys.stdin.readline().rstrip("\n")


@ai_app.command("set-key")
def set_key(
    ctx: typer.Context,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="Provider name (anthropic / openai / google / ollama / openai-compatible).",
        ),
    ],
    key_stdin: Annotated[
        bool,
        typer.Option(
            "--key-stdin",
            help="Read the API key from stdin (one line, no echo handling). For scripts.",
        ),
    ] = False,
) -> None:
    """Upload an API key for ``provider`` on this household."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    if key_stdin:
        api_key = _read_key_from_stdin()
    else:
        api_key = getpass.getpass(f"API key for {provider}: ")
    if not api_key:
        typer.echo("ai set-key: empty key; nothing stored.", err=True)
        raise typer.Exit(1)
    try:
        with _client(config, as_json=as_json) as client:
            client.post(
                f"/v1/ai/keys/{provider}",
                json={"api_key": api_key},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    typer.echo(f"ai set-key: stored key for provider {provider!r}.")


@ai_app.command("forget-key")
def forget_key(
    ctx: typer.Context,
    provider: Annotated[str, typer.Option("--provider", help="Provider name.")],
) -> None:
    """Remove the household's API key for ``provider``. Idempotent on missing keys."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            client.request("DELETE", f"/v1/ai/keys/{provider}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    typer.echo(f"ai forget-key: removed key for provider {provider!r}.")


@ai_app.command("list-keys")
def list_keys(ctx: typer.Context) -> None:
    """List providers that have keys configured. Does NOT show the keys."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/ai/keys", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    providers = response.json().get("providers", [])
    if not providers:
        typer.echo("No AI keys configured. Run `tulip ai set-key --provider X`.")
        return
    typer.echo("Configured providers:")
    for p in providers:
        typer.echo(f"  - {p}")


@ai_app.command("status")
def status(ctx: typer.Context) -> None:
    """Show the resolved AI policy for this household."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/ai/status", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(f"AI status — {config.api_url}")
    typer.echo(f"  default provider: {body.get('default_provider') or '(unset)'}")
    typer.echo(f"  default model:    {body.get('default_model') or '(unset)'}")
    cap = body.get("monthly_cost_cap_usd")
    typer.echo(f"  monthly cap USD:  {cap if cap is not None else '(unlimited)'}")
    typer.echo(f"  log prompts:      {body.get('log_prompts')}")
    providers = ", ".join(body.get("providers_with_keys") or []) or "(none)"
    typer.echo(f"  providers w/keys: {providers}")
    typer.echo("  capabilities:")
    for name, cfg in (body.get("capabilities") or {}).items():
        typer.echo(
            f"    {name:14s} level={cfg.get('level')} profile={cfg.get('profile')} "
            f"provider={cfg.get('provider') or '(inherit)'}"
        )


@ai_app.command("ask")
def ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="Natural-language question to ask the AI.")],
) -> None:
    """Run an NL query against the AI views (P6.2)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                "/v1/ai/ask",
                json={"question": question},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    if body.get("error"):
        typer.echo(f"ai ask: {body['error']}", err=True)
        raise typer.Exit(1)
    if body.get("summary"):
        typer.echo(body["summary"])
    else:
        typer.echo("(no answer)")
    if body.get("rows"):
        typer.echo("")
        typer.echo(f"Rows ({len(body['rows'])}):")
        typer.echo(json.dumps(body["rows"], indent=2, ensure_ascii=False))


@ai_app.command("propose")
def propose(
    ctx: typer.Context,
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="Proposal kind (e.g. envelope_budget_update).",
        ),
    ],
    title: Annotated[str, typer.Option("--title", help="Short headline.")],
    payload: Annotated[
        str,
        typer.Option(
            "--payload",
            help="JSON object describing the change. Kind-specific shape.",
        ),
    ],
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Optional free-text justification."),
    ] = "",
) -> None:
    """Create a pending proposal (manual; AI-generated proposals land in P6.4.b)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        payload_obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--payload must be valid JSON: {exc}") from exc

    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                "/v1/ai/proposals",
                json={
                    "kind": kind,
                    "title": title,
                    "payload": payload_obj,
                    "rationale": rationale,
                },
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(f"Created proposal {body['id']} ({body['kind']}): {body['title']}")


@ai_app.command("proposals")
def list_proposals(
    ctx: typer.Context,
    status: Annotated[
        str,
        typer.Option(
            "--status",
            help="Filter: pending / approved / rejected. Empty string for all.",
        ),
    ] = "pending",
) -> None:
    """List proposals for the household, newest first."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(
                "/v1/ai/proposals",
                authenticated=True,
                params={"status": status},
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    rows = response.json()
    if not rows:
        typer.echo("No proposals.")
        return
    for r in rows:
        typer.echo(f"  {r['id'][:8]}  {r['kind']:32s}  {r['status']:10s}  {r['title']}")


@ai_app.command("approve")
def approve_proposal(
    ctx: typer.Context,
    proposal_id: Annotated[str, typer.Argument(help="Proposal UUID.")],
    note: Annotated[str, typer.Option("--note", help="Optional decision note.")] = "",
) -> None:
    """Approve a proposal and execute its change."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    body: dict[str, str] = {"note": note} if note else {}
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/ai/proposals/{proposal_id}/approve",
                json=body,
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body_json = response.json()
    typer.echo(f"Approved proposal {body_json['id']} ({body_json['kind']}).")


@ai_app.command("reject")
def reject_proposal(
    ctx: typer.Context,
    proposal_id: Annotated[str, typer.Argument(help="Proposal UUID.")],
    note: Annotated[str, typer.Option("--note", help="Optional decision note.")] = "",
) -> None:
    """Reject a proposal. No state change beyond the proposal row."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    body: dict[str, str] = {"note": note} if note else {}
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/ai/proposals/{proposal_id}/reject",
                json=body,
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body_json = response.json()
    typer.echo(f"Rejected proposal {body_json['id']} ({body_json['kind']}).")


@ai_app.command("preview")
def preview(
    ctx: typer.Context,
    description: Annotated[str, typer.Option("--description", help="Sample line description.")],
    amount: Annotated[str, typer.Option("--amount", help="Sample line amount (decimal).")],
    currency: Annotated[str, typer.Option("--currency", help="ISO 4217 code.")] = "USD",
    posted_date: Annotated[
        str,
        typer.Option(
            "--date",
            help="ISO date (defaults to today).",
        ),
    ] = "",
) -> None:
    """Show the exact redacted prompt body that ``categorize`` would send."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    posted = posted_date or date.today().isoformat()
    try:
        Decimal(amount)
    except (ValueError, ArithmeticError) as exc:
        raise typer.BadParameter(f"--amount must be a decimal: {exc}") from exc
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                "/v1/ai/preview",
                json={
                    "description": description,
                    "amount": amount,
                    "currency": currency,
                    "posted_date": posted,
                },
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(f"profile:  {body.get('profile')}")
    typer.echo(f"provider: {body.get('provider') or '(unset)'}")
    typer.echo(f"model:    {body.get('model') or '(unset)'}")
    typer.echo("payload:")
    typer.echo(json.dumps(body.get("payload"), indent=2, ensure_ascii=False))
