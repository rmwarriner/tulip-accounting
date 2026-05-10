"""Unit tests for the interactive password loop in ``tulip register``.

The loop is the part of ``register`` that's hard to drive E2E (TTY-only
prompts) and where most UX decisions live. It takes a ``prompt`` and a
``notice`` callable so the loop logic is testable without real terminal
I/O.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator

from tulip_cli.commands.register import (
    PASSWORD_MIN_LENGTH,
    _acquire_password_interactive,
    _read_password_from_stdin,
)


class _FakeStream(io.StringIO):
    """StringIO with a controllable ``isatty()`` for branching tests."""

    def __init__(self, content: str, *, tty: bool) -> None:
        super().__init__(content)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _scripted_prompt(answers: Iterable[str]) -> Iterator[str]:
    return iter(answers)


def test_acquire_password_returns_after_valid_pair() -> None:
    answers = _scripted_prompt(["this-is-long-enough", "this-is-long-enough"])
    notices: list[str] = []
    result = _acquire_password_interactive(
        prompt=lambda *_a, **_kw: next(answers),
        notice=notices.append,
    )
    assert result == "this-is-long-enough"
    assert notices == []


def test_acquire_password_loops_when_first_input_is_too_short() -> None:
    """Short input → notice + retry, no confirmation prompt for the rejected attempt."""
    answers = _scripted_prompt(
        [
            "short",  # rejected: too short, no confirmation asked
            "this-is-long-enough",  # second attempt
            "this-is-long-enough",  # confirmation
        ]
    )
    notices: list[str] = []
    result = _acquire_password_interactive(
        prompt=lambda *_a, **_kw: next(answers),
        notice=notices.append,
    )
    assert result == "this-is-long-enough"
    assert any(str(PASSWORD_MIN_LENGTH) in n for n in notices)


def test_acquire_password_loops_when_confirmation_mismatches() -> None:
    answers = _scripted_prompt(
        [
            "this-is-long-enough",
            "different-confirmation",  # mismatch — start over
            "second-attempt-password",
            "second-attempt-password",
        ]
    )
    notices: list[str] = []
    result = _acquire_password_interactive(
        prompt=lambda *_a, **_kw: next(answers),
        notice=notices.append,
    )
    assert result == "second-attempt-password"
    assert any("match" in n.lower() for n in notices)


def test_acquire_password_validates_then_confirms_in_that_order() -> None:
    """Length check happens first; a too-short password never reaches confirmation."""
    seen_prompts: list[str] = []
    answers = _scripted_prompt(["short", "this-is-long-enough", "this-is-long-enough"])

    def prompt(text: str, **_kw: object) -> str:
        seen_prompts.append(text)
        return next(answers)

    _acquire_password_interactive(prompt=prompt, notice=lambda _m: None)

    # Three prompts: rejected short pw, valid pw, confirmation. The
    # confirmation prompt should appear exactly once — after a valid pw,
    # not after the short one.
    assert seen_prompts == ["Password", "Password", "Repeat for confirmation"]


def test_read_password_from_stdin_emits_hint_when_tty() -> None:
    """Interactive shell with no redirection: emit a hint so the CLI doesn't look hung."""
    stream = _FakeStream("hunter2hunter2\n", tty=True)
    notices: list[str] = []
    result = _read_password_from_stdin(stream=stream, notice=notices.append)
    assert result == "hunter2hunter2"
    assert len(notices) == 1
    assert "password" in notices[0].lower()


def test_read_password_from_stdin_silent_when_piped() -> None:
    """Pipe / heredoc: no hint, scripts get a clean stderr."""
    stream = _FakeStream("hunter2hunter2\n", tty=False)
    notices: list[str] = []
    result = _read_password_from_stdin(stream=stream, notice=notices.append)
    assert result == "hunter2hunter2"
    assert notices == []
