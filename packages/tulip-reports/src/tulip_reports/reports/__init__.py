"""Per-report modules (P7.1).

Each module exposes a ``build(...) -> ReportData`` data builder and a
``render_html(data) -> str`` HTML renderer. API and CLI layers consume
these directly.
"""
