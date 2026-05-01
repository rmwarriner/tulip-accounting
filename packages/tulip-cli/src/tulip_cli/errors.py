"""RFC 9457 Problem Details rendering and exit-code mapping for the CLI.

ARCHITECTURE.md §7.8.5 specifies that CLI error output mirrors the API's
Problem Details shape: a leading bold red title and an indented ``detail``
paragraph in plain English. Exit codes follow this table:

* ``0`` — success
* ``1`` — user error (request invalid, not found, conflict, validation)
* ``2`` — auth (401, 403)
* ``3`` — server (5xx)
* ``4`` — network (connection refused, DNS failure, timeout)
* ``5`` — configuration (CLI couldn't even attempt the request)

The renderer accepts a Problem Details dict (``application/problem+json``
body shape) and writes either pretty output to stderr or the raw JSON body
to stdout when ``--json`` is set.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Final

import httpx
from rich.console import Console
from rich.text import Text

EXIT_OK: Final[int] = 0
EXIT_USER: Final[int] = 1
EXIT_AUTH: Final[int] = 2
EXIT_SERVER: Final[int] = 3
EXIT_NETWORK: Final[int] = 4
EXIT_CONFIG: Final[int] = 5

PROBLEM_CONTENT_TYPE: Final[str] = "application/problem+json"


def exit_code_for_status(status: int) -> int:
    """Map an HTTP status code to a CLI exit code per §7.8.5."""
    if status in (401, 403):
        return EXIT_AUTH
    if 400 <= status < 500:
        return EXIT_USER
    if 500 <= status < 600:
        return EXIT_SERVER
    return EXIT_USER


def exit_code_for_problem(problem: dict[str, Any]) -> int:
    """Pick an exit code from a Problem Details body.

    ``code`` wins when its prefix indicates a category (``config.*`` →
    exit 5, ``network.*`` → exit 4); otherwise ``status`` decides.
    """
    code = problem.get("code")
    if isinstance(code, str):
        if code.startswith("config."):
            return EXIT_CONFIG
        if code.startswith("network."):
            return EXIT_NETWORK
    status = problem.get("status")
    return exit_code_for_status(int(status)) if isinstance(status, int) else EXIT_USER


def _request_url(response: httpx.Response) -> str:
    """Return the full URL of the request that produced ``response``, or ``""``."""
    try:
        return str(response.request.url)
    except RuntimeError:
        return ""


def _request_path(response: httpx.Response) -> str:
    """Return the path of the request that produced ``response``, or ``""``."""
    try:
        return str(response.request.url.path)
    except RuntimeError:
        return ""


def _is_json_body(content_type: str) -> bool:
    """Return whether the response body claims to be JSON of any flavor."""
    return "json" in content_type.lower()


def parse_problem_response(response: httpx.Response) -> dict[str, Any]:
    """Parse an ``httpx.Response`` into a Problem Details dict.

    Decision order (status first, content-type second):

    1. ``application/problem+json`` → pass through.
    2. **5xx**, regardless of content-type → ``server.unexpected_response``.
       A 500 ``text/plain`` is what Starlette emits when an unhandled
       exception escapes the Problem Details handler — telling the user
       they're misconfigured in that case is wrong and unhelpful.
    3. **4xx with JSON** but not problem-shaped → ``server.unexpected_response``.
       The API is real but speaking a non-RFC-9457 dialect, which is a
       server bug.
    4. **4xx with non-JSON** (HTML, plaintext, etc.) → ``config.not_a_tulip_api``.
       Misconfigured DNS, wrong port, or a reverse proxy returning its own
       404 HTML page. Exit ``5`` so the user fixes their config.
    """
    content_type = response.headers.get("content-type", "")
    if PROBLEM_CONTENT_TYPE in content_type:
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data

    instance = _request_path(response)
    if response.status_code >= 500 or _is_json_body(content_type):
        return {
            "type": "/.well-known/errors/server.unexpected_response",
            "title": "Unexpected response from the API",
            "status": response.status_code,
            "detail": (
                f"The API returned status {response.status_code} with a body that "
                "isn't RFC 9457 Problem Details. This is a server bug — the API "
                "should always emit application/problem+json on errors."
            ),
            "instance": instance,
            "code": "server.unexpected_response",
        }

    url = _request_url(response) or "(no URL)"
    return {
        "type": "/.well-known/errors/config.not_a_tulip_api",
        "title": "That URL doesn't look like a Tulip API",
        "status": response.status_code,
        "detail": (
            f"GET {url} returned status {response.status_code} with content-type "
            f"{content_type or '(unset)'}. A Tulip API always responds with JSON. "
            "Check that --api-url / TULIP_API_URL points at a running Tulip server."
        ),
        "instance": instance,
        "code": "config.not_a_tulip_api",
    }


@dataclass(frozen=True, slots=True)
class CliError(Exception):
    """A renderable CLI failure carrying a Problem Details payload."""

    problem: dict[str, Any]
    as_json: bool
    exit_code: int = EXIT_USER

    def __post_init__(self) -> None:
        """Resolve ``exit_code`` from the problem body if it was left at the default."""
        if self.exit_code == EXIT_USER:
            object.__setattr__(self, "exit_code", exit_code_for_problem(self.problem))

    @classmethod
    def from_response(cls, response: httpx.Response, *, as_json: bool) -> CliError:
        """Build a :class:`CliError` from an ``httpx.Response``."""
        return cls(problem=parse_problem_response(response), as_json=as_json)

    @classmethod
    def from_network_error(cls, exc: httpx.HTTPError, *, as_json: bool) -> CliError:
        """Build a :class:`CliError` from a network-level httpx exception."""
        problem: dict[str, Any] = {
            "type": "/.well-known/errors/network.unreachable",
            "title": "Could not reach the Tulip API",
            "status": 0,
            "detail": (
                f"{type(exc).__name__}: {exc}. Check that the API is running and "
                "that TULIP_API_URL points at it."
            ),
            "instance": "",
            "code": "network.unreachable",
        }
        return cls(problem=problem, as_json=as_json, exit_code=EXIT_NETWORK)

    def render(self) -> None:
        """Write the error to the appropriate stream in the chosen format."""
        if self.as_json:
            sys.stdout.write(json.dumps(self.problem) + "\n")
            return
        console = Console(stderr=True, highlight=False)
        title = Text(str(self.problem.get("title", "Error")), style="bold red")
        console.print(title)
        detail = self.problem.get("detail")
        if detail:
            console.print(Text(f"  {detail}"))
        for line in _format_pydantic_errors(self.problem.get("errors")):
            console.print(Text(f"  - {line}"))
        request_id = self.problem.get("request_id")
        if request_id:
            console.print(Text(f"  request_id: {request_id}", style="dim"))


_LOC_REQUEST_PART_PREFIXES: Final[frozenset[str]] = frozenset({"body", "query", "path", "header"})


def _format_pydantic_errors(errors: object) -> list[str]:
    """Render a Pydantic-shaped ``errors`` extension as ``loc: msg`` lines.

    The API surfaces ``RequestValidationError.errors()`` verbatim under the
    ``errors`` key (RFC 9457 §3.2 extension); each entry has ``loc`` and
    ``msg`` keys at minimum. Anything that doesn't match the shape is
    silently ignored — extension fields are open-ended and the renderer
    shouldn't crash on a payload it doesn't understand.

    Pydantic prefixes ``loc`` with the request part the field came from
    (``"body"``, ``"query"``, ``"path"``, ``"header"``). That's
    implementation jargon to a CLI user, so we strip the leading segment
    when it's one of those — ``["body", "password"]`` renders as
    ``password``; ``["body", "postings", 0, "account_id"]`` as
    ``postings.0.account_id``.
    """
    if not isinstance(errors, list):
        return []
    lines: list[str] = []
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        loc = entry.get("loc")
        msg = entry.get("msg")
        if not isinstance(msg, str):
            continue
        if isinstance(loc, list | tuple) and loc:
            parts = list(loc)
            head = parts[0]
            if isinstance(head, str) and head in _LOC_REQUEST_PART_PREFIXES and len(parts) > 1:
                parts = parts[1:]
            location = ".".join(str(part) for part in parts)
            lines.append(f"{location}: {msg}")
        else:
            lines.append(msg)
    return lines
