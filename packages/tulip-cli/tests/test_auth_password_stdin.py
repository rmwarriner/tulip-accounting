"""Unit tests for ``tulip auth login --password-stdin`` TTY-vs-pipe behaviour.

The integration tests in ``test_auth_login.py`` always pipe stdin (subprocess
with ``input=...``), which means they only exercise the non-TTY branch. These
tests cover the TTY branch directly so we don't need a pty-driving subprocess.
"""

from __future__ import annotations

import io

from tulip_cli.commands.auth import _read_password_from_stdin


class _FakeStream(io.StringIO):
    def __init__(self, content: str, *, tty: bool) -> None:
        super().__init__(content)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_read_password_from_stdin_emits_hint_when_tty() -> None:
    stream = _FakeStream("hunter2hunter2\n", tty=True)
    notices: list[str] = []
    result = _read_password_from_stdin(stream=stream, notice=notices.append)
    assert result == "hunter2hunter2"
    assert len(notices) == 1
    assert "password" in notices[0].lower()


def test_read_password_from_stdin_silent_when_piped() -> None:
    stream = _FakeStream("hunter2hunter2\n", tty=False)
    notices: list[str] = []
    result = _read_password_from_stdin(stream=stream, notice=notices.append)
    assert result == "hunter2hunter2"
    assert notices == []
