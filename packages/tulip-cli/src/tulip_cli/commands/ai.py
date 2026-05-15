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
    if cap is not None:
        mtd = body.get("month_to_date_spend_usd")
        typer.echo(f"  spent this month: {mtd if mtd is not None else '0'}")
    typer.echo(f"  cost cap behaviour: {body.get('cost_cap_behaviour', 'degrade')}")
    typer.echo(f"  rate limit / hour:  {body.get('rate_limit_per_hour', 60)}")
    fallback_provider = body.get("fallback_provider")
    fallback_model = body.get("fallback_model")
    if fallback_provider:
        typer.echo(
            f"  fallback provider:  {fallback_provider}"
            f"{f' ({fallback_model})' if fallback_model else ''}"
        )
        typer.echo(
            "    NOTE: applies on cost-cap degrade ONLY. Provider 5xx errors "
            "do NOT silently fall back (ADR-0005 §Q8)."
        )
    else:
        typer.echo("  fallback provider:  (unset)")
    log_prompts = body.get("log_prompts")
    typer.echo(f"  log prompts:      {log_prompts}")
    if log_prompts:
        typer.echo(
            "    WARNING: prompts + responses are stored in ai_invocations. "
            "Forensic value, privacy cost (ADR-0005 §Q6)."
        )
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


@ai_app.command("suggest-budget")
def suggest_budget(
    ctx: typer.Context,
    envelope_id: Annotated[
        str,
        typer.Option(
            "--envelope",
            help="Envelope UUID. Use `tulip envelopes list` to find it.",
        ),
    ],
) -> None:
    """Ask AI to propose a new budget for ``envelope`` based on recent spend (P6.4.b).

    On success the suggestion lands in your proposal inbox as
    ``kind=envelope_budget_update`` with ``created_by_kind=ai_agent``.
    Approve via ``tulip ai approve UUID`` or reject via
    ``tulip ai reject UUID``.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                "/v1/ai/proposals/suggest/budget",
                json={"envelope_id": envelope_id},
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
        typer.echo(f"ai suggest-budget: {body['error']}", err=True)
        raise typer.Exit(1)
    proposal = body["proposal"]
    typer.echo(f"Created proposal {proposal['id']}: {proposal['title']}")
    if proposal.get("rationale"):
        typer.echo(f"  rationale: {proposal['rationale']}")
    typer.echo(
        f"  Review with `tulip ai approve {proposal['id']}` or `tulip ai reject {proposal['id']}`."
    )


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


# --- P6.5.b: `tulip ai config` editor -------------------------------------

config_app = typer.Typer(
    name="config",
    help="Edit the household ai_policy JSON (admin-only).",
    no_args_is_help=True,
)
ai_app.add_typer(config_app)

# Whitelisted household-level keys for `set` / `clear`. Maps user-facing
# name to the wire-level field name (currently 1:1; the indirection lets
# us rename without breaking the CLI surface).
_HOUSEHOLD_KEYS: dict[str, str] = {
    "default_provider": "default_provider",
    "default_model": "default_model",
    "profile": "profile",
    "monthly_cost_cap_usd": "monthly_cost_cap_usd",
    "cost_cap_behaviour": "cost_cap_behaviour",
    "rate_limit_per_hour": "rate_limit_per_hour",
    "fallback_provider": "fallback_provider",
    "fallback_model": "fallback_model",
    "log_prompts": "log_prompts",
}

# Same idea for `set-capability`.
_CAPABILITY_FIELDS: dict[str, str] = {
    "policy": "policy",
    "provider": "provider",
    "model": "model",
    "profile": "profile",
}

_CAPABILITIES = ("categorize", "nl_query", "forecast", "agentic")


def _coerce_set_value(key: str, value: str) -> object:
    """Coerce a raw CLI string to the wire-level type for ``key``.

    The API accepts a string for ``monthly_cost_cap_usd`` (so the
    ``__CLEAR__`` sentinel can share the field) and a bool / int / str
    for the others. The empty string is the "clear" sentinel.
    """
    if value == "" or value == "__CLEAR__":
        return "__CLEAR__"
    if key == "log_prompts":
        if value.lower() in ("true", "1", "on", "yes"):
            return True
        if value.lower() in ("false", "0", "off", "no"):
            return False
        raise typer.BadParameter(f"log_prompts must be true/false, got {value!r}")
    if key == "rate_limit_per_hour":
        try:
            n = int(value)
        except ValueError as exc:
            raise typer.BadParameter(f"rate_limit_per_hour must be int, got {value!r}") from exc
        return n
    return value


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print the household-level ai_policy + per-capability overrides."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/ai/config", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo("AI config (household-level):")
    for name in _HOUSEHOLD_KEYS:
        value = body.get(name)
        typer.echo(f"  {name:24s} = {value if value is not None else '(unset)'}")
    retention = body.get("invocation_retention_days")
    if retention is not None:
        # Read-only — set by the server's ai_retention handler, not via `set`.
        typer.echo(
            f"  {'invocation_retention':24s} = {retention} days "
            "(non-proposal ai_invocations; read-only)"
        )
    typer.echo("Per-capability overrides:")
    for cap in _CAPABILITIES:
        cfg = (body.get("capabilities") or {}).get(cap) or {}
        non_default = {k: v for k, v in cfg.items() if v is not None}
        if non_default:
            typer.echo(f"  {cap}: {json.dumps(non_default, ensure_ascii=False)}")
        else:
            typer.echo(f"  {cap}: (inherit)")


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Household-level key, e.g. default_provider.")],
    value: Annotated[
        str,
        typer.Argument(help="New value. Empty string or '__CLEAR__' removes the key."),
    ],
) -> None:
    """Set one household-level ai_policy key."""
    if key not in _HOUSEHOLD_KEYS:
        typer.echo(
            f"unknown key {key!r}. Known: {', '.join(sorted(_HOUSEHOLD_KEYS))}",
            err=True,
        )
        raise typer.Exit(1)
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    coerced = _coerce_set_value(key, value)
    try:
        with _client(config, as_json=as_json) as client:
            response = client.put(
                "/v1/ai/config",
                json={key: coerced},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(
        f"ai config: set {key} = {coerced!r}"
        if coerced != "__CLEAR__"
        else f"ai config: cleared {key}"
    )


@config_app.command("clear")
def config_clear(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Household-level key to clear.")],
) -> None:
    """Remove one household-level ai_policy key. Idempotent."""
    if key not in _HOUSEHOLD_KEYS:
        typer.echo(
            f"unknown key {key!r}. Known: {', '.join(sorted(_HOUSEHOLD_KEYS))}",
            err=True,
        )
        raise typer.Exit(1)
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.put(
                "/v1/ai/config",
                json={key: "__CLEAR__"},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"ai config: cleared {key}")


@config_app.command("set-capability")
def config_set_capability(
    ctx: typer.Context,
    capability: Annotated[
        str,
        typer.Argument(help="One of categorize / nl_query / forecast / agentic."),
    ],
    field: Annotated[str, typer.Argument(help="One of policy / provider / model / profile.")],
    value: Annotated[
        str,
        typer.Argument(help="New value. Empty string or '__CLEAR__' removes the override."),
    ],
) -> None:
    """Override one ai_policy field for a single capability."""
    if capability not in _CAPABILITIES:
        typer.echo(
            f"unknown capability {capability!r}. Known: {', '.join(_CAPABILITIES)}",
            err=True,
        )
        raise typer.Exit(1)
    if field not in _CAPABILITY_FIELDS:
        typer.echo(
            f"unknown field {field!r}. Known: {', '.join(sorted(_CAPABILITY_FIELDS))}",
            err=True,
        )
        raise typer.Exit(1)
    coerced: object = value if value != "" else "__CLEAR__"
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.put(
                f"/v1/ai/config/capabilities/{capability}",
                json={field: coerced},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"ai config: {capability}.{field} = {coerced!r}")


@config_app.command("log-prompts")
def config_log_prompts(
    ctx: typer.Context,
    state: Annotated[
        str, typer.Argument(help="Either 'on' or 'off' (sets ai_policy.log_prompts).")
    ],
) -> None:
    """Toggle ai_policy.log_prompts. Prints a warning when turning it on."""
    if state not in ("on", "off"):
        typer.echo(f"state must be 'on' or 'off', got {state!r}", err=True)
        raise typer.Exit(1)
    enable = state == "on"
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.put(
                "/v1/ai/config",
                json={"log_prompts": enable},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"ai config: log_prompts = {enable}")
    if enable:
        typer.echo(
            "WARNING: full prompts + responses will now be stored in ai_invocations. "
            "Forensic value, privacy cost (ADR-0005 §Q6).",
            err=True,
        )
