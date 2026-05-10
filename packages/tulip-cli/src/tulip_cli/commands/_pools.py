"""Resolvers + Problem Details shapes for envelope / sinking-fund / pool lookups.

Mirrors the ``_resolve_account`` pattern in :mod:`tulip_cli.commands.accounts`,
adapted for resources that have no ``code`` field — fallback is on ``name``.
Three resolvers live here so the top-level ``transfer`` command can import
without a circular reference back to the envelopes / sinking_funds modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from tulip_cli.errors import EXIT_USER, CliError

if TYPE_CHECKING:
    from tulip_cli.http import TulipClient


def _not_found_problem(kind: str, identifier: str) -> dict[str, object]:
    return {
        "type": f"/.well-known/errors/{kind}.not_found",
        "title": f"{kind.replace('_', ' ').capitalize()} not found",
        "status": 0,
        "detail": (
            f"No {kind.replace('_', ' ')} with name or id {identifier!r} is "
            f"visible to this user. Run `tulip {kind.replace('_', '-')}s list` "
            "to see what's available."
        ),
        "instance": "",
        "code": f"{kind}.not_found",
    }


def _ambiguous_name_problem(kind: str, identifier: str, count: int) -> dict[str, object]:
    return {
        "type": f"/.well-known/errors/{kind}.ambiguous_name",
        "title": f"{kind.replace('_', ' ').capitalize()} name matches multiple",
        "status": 0,
        "detail": (
            f"{count} {kind.replace('_', ' ')}s have name {identifier!r}. "
            "Use the UUID to disambiguate, or rename one of the duplicates."
        ),
        "instance": "",
        "code": f"{kind}.ambiguous_name",
    }


def _ambiguous_pool_across_types(identifier: str) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/pool.ambiguous_name",
        "title": "Pool name matches across types",
        "status": 0,
        "detail": (
            f"Name {identifier!r} matches both an envelope and a sinking fund. "
            "Use the UUID to disambiguate, or rename one of them."
        ),
        "instance": "",
        "code": "pool.ambiguous_name",
    }


def _looks_like_uuid(identifier: str) -> bool:
    try:
        UUID(identifier)
    except ValueError:
        return False
    return True


def _resolve_envelope(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single envelope dict by UUID or by ``name``.

    UUID lookup hits ``GET /v1/envelopes/{id}`` directly. Name lookup
    fetches the list and matches on ``name``; ambiguous matches raise
    rather than picking the first.
    """
    if _looks_like_uuid(identifier):
        response = client.get(f"/v1/envelopes/{identifier}", authenticated=True)
        return dict(response.json())

    response = client.get("/v1/envelopes", authenticated=True)
    matches = [e for e in response.json() if e.get("name") == identifier]
    if not matches:
        raise CliError(
            problem=_not_found_problem("envelope", identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(matches) > 1:
        raise CliError(
            problem=_ambiguous_name_problem("envelope", identifier, len(matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    return dict(matches[0])


def _resolve_sinking_fund(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single sinking-fund dict by UUID or by ``name``."""
    if _looks_like_uuid(identifier):
        response = client.get(f"/v1/sinking-funds/{identifier}", authenticated=True)
        return dict(response.json())

    response = client.get("/v1/sinking-funds", authenticated=True)
    matches = [s for s in response.json() if s.get("name") == identifier]
    if not matches:
        raise CliError(
            problem=_not_found_problem("sinking_fund", identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(matches) > 1:
        raise CliError(
            problem=_ambiguous_name_problem("sinking_fund", identifier, len(matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    return dict(matches[0])


def _resolve_pool(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single pool dict (envelope or sinking-fund) by UUID or name.

    UUID-shaped identifiers try the envelope endpoint first; if that 404s
    (which surfaces as a ``CliError``), try the sinking-fund endpoint. For
    name-shaped identifiers, we fetch both lists and match on ``name``;
    cross-type collisions raise ``pool.ambiguous_name``.

    Returns the pool dict in whatever shape its source endpoint returned
    (envelope or sinking_fund). Callers that need the pool's currency or
    id can rely on those fields existing in both shapes.
    """
    if _looks_like_uuid(identifier):
        try:
            response = client.get(f"/v1/envelopes/{identifier}", authenticated=True)
            return dict(response.json())
        except CliError as env_err:
            # Re-raise anything that isn't a not-found — auth errors,
            # network failures shouldn't be re-tried as the wrong endpoint.
            if env_err.problem.get("code") != "envelope.not_found":
                raise
        # Fall through to sinking-fund lookup. If THAT also 404s, we
        # surface a generic pool.not_found rather than the SF-specific one.
        try:
            response = client.get(f"/v1/sinking-funds/{identifier}", authenticated=True)
            return dict(response.json())
        except CliError as sf_err:
            if sf_err.problem.get("code") != "sinking_fund.not_found":
                raise
            raise CliError(
                problem=_not_found_problem("pool", identifier),
                as_json=False,
                exit_code=EXIT_USER,
            ) from sf_err

    envelopes = client.get("/v1/envelopes", authenticated=True).json()
    sinking_funds = client.get("/v1/sinking-funds", authenticated=True).json()
    env_matches = [e for e in envelopes if e.get("name") == identifier]
    sf_matches = [s for s in sinking_funds if s.get("name") == identifier]

    if env_matches and sf_matches:
        raise CliError(
            problem=_ambiguous_pool_across_types(identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if not env_matches and not sf_matches:
        raise CliError(
            problem=_not_found_problem("pool", identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    matches = env_matches or sf_matches
    if len(matches) > 1:
        raise CliError(
            problem=_ambiguous_name_problem("pool", identifier, len(matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    return dict(matches[0])


def _summarize_refill_rule(rule: dict[str, Any] | None) -> str:
    """One-line description of an envelope's ``refill_rule`` (#137).

    Returns ``"—"`` for envelopes with no rule. Each strategy gets a
    compact form keyed by what a user actually scans for: the per-cycle
    amount or percentage.
    """
    if not rule:
        return "—"
    strategy = rule.get("strategy")
    if strategy == "fixed_amount":
        return f"fixed: {rule.get('amount', '?')} {rule.get('currency', '')}".rstrip()
    if strategy == "fill_to_amount":
        return f"target: {rule.get('amount', '?')} {rule.get('currency', '')}".rstrip()
    if strategy == "percentage_of_income":
        # Stored as a decimal fraction (e.g. 0.05 for 5%).
        pct = rule.get("percentage")
        if pct is None:
            return "pct-inflow"
        try:
            display = f"{float(pct) * 100:g}%"
        except (TypeError, ValueError):
            display = str(pct)
        return f"pct-inflow: {display}"
    return str(strategy or "—")
