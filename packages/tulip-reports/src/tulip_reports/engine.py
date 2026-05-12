"""Jinja2-based rendering engine for tulip-reports (P7.1).

Each report module loads its template through :class:`ReportRenderer`,
which wraps a single :class:`jinja2.Environment` shared across reports.
The environment is configured with:

- ``autoescape=True`` (HTML output; safe-by-default)
- ``trim_blocks`` / ``lstrip_blocks`` (cleaner rendered output)
- A ``FileSystemLoader`` pointing at the package's ``templates/`` dir
- Custom filters for ``Decimal`` formatting (banker's rounding,
  thousand-separators) and date formatting (ISO 8601)

Toner-friendly CSS rules from ARCHITECTURE.md §8 live in
``templates/base.html`` — every report extends that base, so the rules
apply uniformly. The base template documents which CSS classes
participate in the toner-friendly contract.

The renderer is a module-level singleton; the test seam is the
``base_dir`` kwarg, which tests can point at a fixture-templates dir
if they want to render against synthetic templates.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

_TEMPLATES_DIR: Final[Path] = Path(__file__).resolve().parent / "templates"


def _format_money(value: object, currency: str | None = None) -> str:
    """Render a Decimal-like value with thousand separators + 2 decimals.

    Negative values get a leading minus sign (not parens; parens are
    accountancy convention but make scanning harder on screen). Currency
    is appended when provided so callers can pass per-row currencies.
    """
    if value is None:
        return ""
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (ArithmeticError, ValueError):
            return str(value)
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    sign = "-" if quantized < 0 else ""
    absolute = abs(quantized)
    formatted = f"{absolute:,.2f}"
    out = f"{sign}{formatted}"
    if currency:
        out += f" {currency}"
    return out


def _format_date(value: object) -> str:
    """Render a date / datetime as ISO 8601 ``YYYY-MM-DD``."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_type):
        return value.isoformat()
    return str(value)


def _is_negative(value: object) -> bool:
    """Jinja2 test filter: detect negative Decimal-like values.

    Used by templates to apply the toner-friendly "color reserved for
    emphasis" rule — negative balances render in red. Returns False for
    None / non-numeric values so templates can use it defensively.
    """
    if value is None:
        return False
    if isinstance(value, Decimal):
        return value < 0
    try:
        return Decimal(str(value)) < 0
    except (ArithmeticError, ValueError):
        return False


class ReportRenderer:
    """Jinja2 environment + report-rendering helpers.

    One instance per package is enough; reuse via :func:`get_renderer`.
    Tests that need a different template root construct their own
    instance with ``base_dir=``.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        """Build a Jinja2 env pointed at ``base_dir`` (default: package templates)."""
        loader = FileSystemLoader(str(base_dir or _TEMPLATES_DIR))
        self._env = Environment(
            loader=loader,
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )
        self._env.filters["money"] = _format_money
        self._env.filters["isodate"] = _format_date
        self._env.tests["negative"] = _is_negative

    def render(self, template_name: str, **context: object) -> str:
        """Render ``template_name`` with the given context as HTML."""
        template = self._env.get_template(template_name)
        return template.render(**context)


_RENDERER: ReportRenderer | None = None


def get_renderer() -> ReportRenderer:
    """Return the package-level :class:`ReportRenderer` singleton."""
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = ReportRenderer()
    return _RENDERER
