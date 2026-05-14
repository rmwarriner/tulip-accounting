"""Unit tests for the CLI notes surface (issue #271).

Covers the pure helpers — buffer rendering, notes-block extraction, and
the ``show`` Notes line — without spinning up the live API. Round-trip
behavior end-to-end lives in the API tests.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import typer

from tulip_cli.commands.transactions import (
    _NOTES_BLOCK_END,
    _NOTES_BLOCK_START,
    _UNSET,
    _extract_notes_block,
    _render_notes_block,
    _render_tx_detail,
    _render_tx_for_edit,
)


class TestRenderTxDetailNotes:
    def test_show_prints_notes_when_present(self) -> None:
        buf = io.StringIO()
        tx = {
            "id": "abc",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "posted",
            "notes": "Reimbursed by Carol.",
            "postings": [],
        }
        with redirect_stdout(buf):
            try:
                _render_tx_detail(tx)
            except typer.Exit:
                pass
        out = buf.getvalue()
        assert "Notes:" in out
        assert "Reimbursed by Carol." in out

    def test_show_omits_notes_when_absent(self) -> None:
        buf = io.StringIO()
        tx = {
            "id": "abc",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "posted",
            "notes": None,
            "postings": [],
        }
        with redirect_stdout(buf):
            try:
                _render_tx_detail(tx)
            except typer.Exit:
                pass
        out = buf.getvalue()
        assert "Notes:" not in out

    def test_show_omits_notes_when_key_missing(self) -> None:
        buf = io.StringIO()
        tx = {
            "id": "abc",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "posted",
            "postings": [],
        }
        with redirect_stdout(buf):
            try:
                _render_tx_detail(tx)
            except typer.Exit:
                pass
        out = buf.getvalue()
        assert "Notes:" not in out

    def test_show_indents_multiline_notes(self) -> None:
        buf = io.StringIO()
        tx = {
            "id": "abc",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "posted",
            "notes": "line one\nline two",
            "postings": [],
        }
        with redirect_stdout(buf):
            try:
                _render_tx_detail(tx)
            except typer.Exit:
                pass
        out = buf.getvalue()
        assert "Notes:        line one" in out
        assert "              line two" in out


class TestRenderNotesBlock:
    def test_rendered_block_has_markers(self) -> None:
        block = _render_notes_block("hello")
        assert block[0] == _NOTES_BLOCK_START
        assert block[-1] == _NOTES_BLOCK_END

    def test_rendered_content_lines_are_comment_prefixed(self) -> None:
        block = _render_notes_block("hello\nworld")
        assert "# hello" in block
        assert "# world" in block

    def test_none_renders_empty_block(self) -> None:
        block = _render_notes_block(None)
        assert block == [_NOTES_BLOCK_START, _NOTES_BLOCK_END]


class TestExtractNotesBlock:
    def test_no_block_returns_unset(self) -> None:
        buf = "2026-05-13 lunch\n  food  10.00 USD\n  cash  -10.00 USD\n"
        stripped, notes = _extract_notes_block(buf)
        assert stripped == buf
        assert notes is _UNSET

    def test_empty_block_returns_none(self) -> None:
        buf = (
            "2026-05-13 lunch\n"
            "  food  10.00 USD\n"
            "  cash  -10.00 USD\n"
            f"{_NOTES_BLOCK_START}\n"
            f"{_NOTES_BLOCK_END}\n"
        )
        stripped, notes = _extract_notes_block(buf)
        assert notes is None
        assert _NOTES_BLOCK_START not in stripped
        assert _NOTES_BLOCK_END not in stripped

    def test_block_with_content_returns_plaintext(self) -> None:
        buf = (
            "2026-05-13 lunch\n"
            "  food  10.00 USD\n"
            "  cash  -10.00 USD\n"
            f"{_NOTES_BLOCK_START}\n"
            "# Reimbursed by Carol.\n"
            "# See email thread.\n"
            f"{_NOTES_BLOCK_END}\n"
        )
        stripped, notes = _extract_notes_block(buf)
        assert notes == "Reimbursed by Carol.\nSee email thread."
        assert _NOTES_BLOCK_START not in stripped

    def test_bare_hash_yields_empty_inner_line(self) -> None:
        buf = f"{_NOTES_BLOCK_START}\n# line a\n#\n# line c\n{_NOTES_BLOCK_END}\n"
        _, notes = _extract_notes_block(buf)
        assert notes == "line a\n\nline c"


class TestRenderTxForEdit:
    def test_buffer_round_trips_with_existing_notes(self) -> None:
        tx = {
            "id": "11111111-1111-1111-1111-111111111111",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "pending",
            "notes": "Reimbursed by Carol.",
            "postings": [
                {
                    "account_id": "22222222-2222-2222-2222-222222222222",
                    "amount": "10.00",
                    "currency": "USD",
                    "memo": None,
                },
                {
                    "account_id": "33333333-3333-3333-3333-333333333333",
                    "amount": "-10.00",
                    "currency": "USD",
                    "memo": None,
                },
            ],
        }
        buf = _render_tx_for_edit(tx, accounts_by_id={})
        # Notes block is present, with the plaintext embedded as a # comment.
        assert _NOTES_BLOCK_START in buf
        assert "# Reimbursed by Carol." in buf
        assert _NOTES_BLOCK_END in buf

        # Round-trip: extract then verify.
        _, notes = _extract_notes_block(buf)
        assert notes == "Reimbursed by Carol."

    def test_buffer_without_notes_still_emits_empty_block(self) -> None:
        """An empty block means the editor flow always offers a place to add notes."""
        tx = {
            "id": "11111111-1111-1111-1111-111111111111",
            "date": "2026-05-13",
            "description": "Lunch",
            "reference": None,
            "status": "pending",
            "notes": None,
            "postings": [
                {
                    "account_id": "22222222-2222-2222-2222-222222222222",
                    "amount": "10.00",
                    "currency": "USD",
                    "memo": None,
                },
                {
                    "account_id": "33333333-3333-3333-3333-333333333333",
                    "amount": "-10.00",
                    "currency": "USD",
                    "memo": None,
                },
            ],
        }
        buf = _render_tx_for_edit(tx, accounts_by_id={})
        assert _NOTES_BLOCK_START in buf
        assert _NOTES_BLOCK_END in buf
        _, notes = _extract_notes_block(buf)
        # Empty block means "explicitly clear" — None.
        assert notes is None
