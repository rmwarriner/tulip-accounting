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
    """Pick an exit code from a Problem Details body (uses ``status``)."""
    status = problem.get("status")
    return exit_code_for_status(int(status)) if isinstance(status, int) else EXIT_USER


def parse_problem_response(response: httpx.Response) -> dict[str, Any]:
    """Parse an ``httpx.Response`` into a Problem Details dict.

    If the response really is ``application/problem+json``, return the
    decoded body. Otherwise synthesize a minimal Problem Details dict so
    the renderer has something consistent to display — surprising server
    output should still produce an actionable error, never a stack trace.
    """
    content_type = response.headers.get("content-type", "")
    if PROBLEM_CONTENT_TYPE in content_type:
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
    try:
        instance = str(response.request.url.path)
    except RuntimeError:
        instance = ""
    return {
        "type": "/.well-known/errors/server.unexpected_response",
        "title": "Unexpected response from the API",
        "status": response.status_code,
        "detail": (
            f"The API returned status {response.status_code} with an unexpected body. "
            "This is a bug in the server or a sign that you are pointed at the wrong URL."
        ),
        "instance": instance,
        "code": "server.unexpected_response",
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
        request_id = self.problem.get("request_id")
        if request_id:
            console.print(Text(f"  request_id: {request_id}", style="dim"))
