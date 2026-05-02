"""GET /.well-known/errors/ and /.well-known/errors/{code}.

Renders human-readable HTML pages for each registered Problem Details
error code. Source of truth is the tree of :class:`tulip_api.errors.TulipProblem`
subclasses — instantiating each (with placeholder args where needed) gives
the canonical title / status / detail.

Adding a new error subclass with a no-arg constructor (or one whose args
have placeholder substitutes below) auto-publishes a page; no separate
registry to maintain.
"""

from __future__ import annotations

import inspect
from html import escape
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from tulip_api.errors import TulipProblem

router = APIRouter(prefix="/.well-known/errors", tags=["docs"])


# Placeholder arguments for subclasses whose constructors take parameters.
# Adding a new subclass that needs args means adding an entry here so the
# docs page can instantiate it. Without an entry, the subclass is skipped
# (we surface a warning in the listing comment, not a hard error — error
# subclasses shouldn't be coupled to docs availability).
_PLACEHOLDER_ARGS: dict[str, dict[str, Any]] = {
    "MfaRequiredError": {"mfa_token": "<mfa-challenge-jwt>", "expires_in": 300},
    "AccountUnknownError": {"account_id": "<account-uuid>"},
    "AccountParentTypeMismatchError": {"child_type": "expense", "parent_type": "asset"},
    "AccountParentCurrencyMismatchError": {
        "child_currency": "EUR",
        "parent_currency": "USD",
    },
    "TransactionInvalidError": {"reason": "<domain-validation-message>"},
    "TransactionUnbalancedError": {
        "reason": "Transaction does not balance: USD postings sum to 1.00 instead of 0."
    },
    "PeriodClosedError": {"reason": "Period 2025-12-31 is closed."},
    "PoolNotFoundError": {"pool_id": "<pool-uuid>"},
    "PoolInactiveError": {"pool_id": "<pool-uuid>"},
    "PoolCurrencyMismatchError": {
        "pool_id": "<pool-uuid>",
        "pool_currency": "USD",
        "posting_currency": "EUR",
    },
    "PoolInvalidAccountTypePairingError": {"account_type": "asset"},
    "PoolTransferCurrencyMismatchError": {
        "src_currency": "USD",
        "dest_currency": "EUR",
    },
    "PoolTransferSystemPoolForbiddenError": {"role": "source"},
    "PoolInflowCurrencyUnknownError": {"currency": "ZZZ"},
    "ValidationFailedError": {
        "errors": [
            {
                "type": "missing",
                "loc": ["body", "email"],
                "msg": "Field required",
                "input": {},
            }
        ],
    },
}


def _all_subclasses(cls: type) -> set[type]:
    """Return ``cls`` and every transitive subclass."""
    result: set[type] = set()
    stack = [cls]
    while stack:
        c = stack.pop()
        for sub in c.__subclasses__():
            if sub not in result:
                result.add(sub)
                stack.append(sub)
    return result


def _instantiate(subclass: type[TulipProblem]) -> TulipProblem | None:
    """Build an instance of ``subclass`` for documentation purposes.

    Returns None if the subclass needs constructor args we haven't
    registered a placeholder for.
    """
    sig = inspect.signature(subclass.__init__)
    required = [
        name
        for name, param in sig.parameters.items()
        if name != "self"
        and param.default is inspect.Parameter.empty
        and param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    ]
    # mypy enforces TulipProblem's parent signature on these calls, but
    # each subclass has its own __init__ that doesn't need those args.
    if not required:
        return subclass()  # type: ignore[call-arg]
    placeholders = _PLACEHOLDER_ARGS.get(subclass.__name__)
    if placeholders is None or not all(name in placeholders for name in required):
        return None
    return subclass(**placeholders)


def _registry() -> dict[str, TulipProblem]:
    """Map ``code`` → an instance carrying the canonical info."""
    out: dict[str, TulipProblem] = {}
    for sub in sorted(_all_subclasses(TulipProblem), key=lambda c: c.__name__):
        instance = _instantiate(sub)
        if instance is None:
            continue
        out[instance.code] = instance
    return out


_PAGE_CSS = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
       margin: 2em auto; padding: 0 1em; color: #222; line-height: 1.5; }
h1 { border-bottom: 1px solid #ccc; padding-bottom: .25em; }
.code { font-family: ui-monospace, Menlo, monospace; background: #f4f4f4;
        padding: .1em .35em; border-radius: 3px; }
.status { color: #666; font-weight: normal; }
.extensions { margin-top: 1em; }
.extensions code { font-family: ui-monospace, Menlo, monospace; }
nav a { text-decoration: none; }
nav a:hover { text-decoration: underline; }
ul { padding-left: 1.5em; }
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def errors_index() -> HTMLResponse:
    """List every documented error code with links to the per-code pages."""
    items = []
    for code, problem in sorted(_registry().items()):
        items.append(
            f'<li><a href="/.well-known/errors/{escape(code)}">'
            f'<span class="code">{escape(code)}</span></a> '
            f'<span class="status">({problem.status})</span> — '
            f"{escape(problem.title)}</li>"
        )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>Tulip Accounting — Error codes</title>
<style>{_PAGE_CSS}</style>
</head><body>
<h1>Tulip Accounting — Error codes</h1>
<p>Each entry is a stable <a href="https://www.rfc-editor.org/rfc/rfc9457">RFC 9457</a>
Problem Details code emitted by this API. Click a code for the full description.</p>
<ul>{"".join(items)}</ul>
</body></html>"""
    )


@router.get("/{code}", response_class=HTMLResponse, include_in_schema=False)
def error_page(code: str) -> HTMLResponse:
    """Render the canonical reference page for one error code."""
    problem = _registry().get(code)
    if problem is None:
        # Browser-targeted 404 — HTML is the right shape, not Problem
        # Details (which is for API clients).
        return HTMLResponse(
            status_code=404,
            content=(
                f"<!doctype html><html lang='en'><head>"
                f"<meta charset='utf-8'><title>Unknown error code</title>"
                f"<style>{_PAGE_CSS}</style></head><body>"
                f"<nav><a href='/.well-known/errors/'>← All error codes</a></nav>"
                f"<h1>Unknown error code</h1>"
                f"<p>No error class with code <code>{escape(code)}</code> "
                f"is registered. See the index for the full list.</p>"
                f"</body></html>"
            ),
        )

    extensions_html = ""
    if problem.extensions:
        rows = "".join(
            f"<li><code>{escape(k)}</code>: <code>{escape(repr(v))}</code></li>"
            for k, v in problem.extensions.items()
        )
        extensions_html = (
            f'<div class="extensions"><h2>Extension fields</h2>'
            f"<p>This error class also surfaces these fields at the top level "
            f"of the response body:</p><ul>{rows}</ul></div>"
        )

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>{escape(problem.code)} — Tulip Accounting error</title>
<style>{_PAGE_CSS}</style>
</head><body>
<nav><a href="/.well-known/errors/">← All error codes</a></nav>
<h1><span class="code">{escape(problem.code)}</span>
<span class="status">— {problem.status}</span></h1>
<h2>{escape(problem.title)}</h2>
<p>{escape(problem.detail)}</p>
{extensions_html}
<p><small>Type URI: <code>{escape(problem.type_uri)}</code></small></p>
</body></html>"""
    )
