"""Reports browser — P9.3 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309).

Left-hand menu of the eight TUI-browseable reports; right-hand content
pane filled with the cursor's report. The on-screen render is the
v1 browse surface — HTML / PDF / CSV exporters remain on the
``tulip reports`` CLI per ADR-0007.

Content rendering is intentionally generic: arrays under any key are
laid out as tables, scalars as ``key: value`` lines. Per-report
hand-tuned views can replace the generic renderer in a later slice
when there's enough TUI usage feedback to know which views need
polish first.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.reports import REPORT_CATALOGUE, ReportPayload, ReportSpec

ReportLoader = Callable[[ReportSpec], ReportPayload]


def _format_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_table(rows: list[object]) -> str:
    """Render ``rows`` as a fixed-width plain-text table.

    Inspect the first row to infer columns. Falls back to single-column
    rendering if rows aren't dict-shaped.
    """
    if not rows:
        return "(no rows)"
    if not isinstance(rows[0], dict):
        return "\n".join(f"  • {_format_value(r)}" for r in rows)
    columns = list(rows[0].keys())
    widths = {col: len(col) for col in columns}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for col in columns:
            widths[col] = max(widths[col], len(_format_value(row.get(col))))
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  ".join("-" * widths[col] for col in columns)
    lines = [header, sep]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append("  ".join(_format_value(row.get(col)).ljust(widths[col]) for col in columns))
    return "\n".join(lines)


def _render_body(body: dict[str, object]) -> str:
    """Render a report body — array sections become tables, scalars become lines."""
    lines: list[str] = []
    for key, value in body.items():
        if isinstance(value, list):
            lines.append(f"[b]{key}[/b]")
            lines.append(_render_table(value))
            lines.append("")
        elif isinstance(value, dict):
            lines.append(f"[b]{key}[/b]")
            for k, v in value.items():
                lines.append(f"  {k}: {_format_value(v)}")
            lines.append("")
        else:
            lines.append(f"{key}: {_format_value(value)}")
    return "\n".join(lines).rstrip()


class ReportsScreen(Screen[None]):
    """Browse the eight ``/v1/reports/*`` reports inside the TUI."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    ReportsScreen {
        layout: vertical;
    }

    ReportsScreen Horizontal {
        height: 1fr;
    }

    ReportsScreen #report-menu {
        width: 32;
        border-right: solid $accent;
    }

    ReportsScreen #report-content {
        width: 1fr;
        padding: 1 2;
    }
    """

    def __init__(self, loader: ReportLoader) -> None:
        """Store the loader; the screen mounts the menu and loads row 0."""
        super().__init__()
        self._loader = loader
        self._menu_rows: list[str] = []
        self._content_text: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the report menu (left) and content pane (right)."""
        yield Header()
        with Horizontal():
            yield DataTable(id="report-menu", zebra_stripes=True, cursor_type="row")
            yield Static("", id="report-content")
        yield Footer()

    def on_mount(self) -> None:
        """Populate the menu and trigger the initial report load."""
        table = self.query_one("#report-menu", DataTable)
        table.add_column("Report")
        self._menu_rows = []
        for spec in REPORT_CATALOGUE:
            table.add_row(spec.title)
            self._menu_rows.append(spec.title)
        self._load_for_cursor()

    def action_refresh(self) -> None:
        """Re-fetch the currently selected report."""
        self._load_for_cursor()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Load the highlighted report into the content pane."""
        self._load_for_cursor()

    # -- internals -----------------------------------------------------

    def _load_for_cursor(self) -> None:
        table = self.query_one("#report-menu", DataTable)
        cursor = table.cursor_row
        index = max(0, cursor)
        if index >= len(REPORT_CATALOGUE):
            return
        spec = REPORT_CATALOGUE[index]
        try:
            payload = self._loader(spec)
        except Exception as exc:
            self._set_content(f"[red]error loading {spec.key}:[/red] {exc}")
            return
        self._set_content(_render_body(payload.body))

    def _set_content(self, text: str) -> None:
        self._content_text = text
        pane = self.query_one("#report-content", Static)
        pane.update(text)

    # -- introspection used by tests ----------------------------------

    def menu_rows(self) -> list[str]:
        """Return the menu row labels in display order."""
        return list(self._menu_rows)

    def content_text(self) -> str:
        """Return the rendered content pane as a plain string."""
        return self._content_text
