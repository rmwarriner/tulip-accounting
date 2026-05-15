"""Report generators for Tulip Accounting (P7.1, ADR-less per slice plan).

Each report module under :mod:`tulip_reports.reports` exposes:

- ``build(session, household_id, ...) -> ReportData``: pure-Python data
  builder reading via the storage repositories.
- ``render_html(data: ReportData) -> str``: Jinja2-rendered HTML using
  the shared base template + toner-friendly print CSS.

The API and CLI layers consume these functions; ``tulip-reports``
itself is pure: it does not import ``tulip-api`` or ``tulip-cli``, and
the architecture test enforces that.

Public surface intentionally narrow — callers import specific report
modules (e.g. ``from tulip_reports.reports import trial_balance``)
rather than going through this package's top level.
"""

from tulip_reports.engine import ReportRenderer, get_renderer

__all__ = ["ReportRenderer", "get_renderer"]
