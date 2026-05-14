"""Unit tests for the ``assert_cli_usage_error`` test helper (#294)."""

from __future__ import annotations

import subprocess

import pytest

from _cli_asserts import assert_cli_usage_error


def _result(
    returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["tulip"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_passes_on_nonzero_exit_with_usage_banner() -> None:
    assert_cli_usage_error(_result(2, stderr="Usage: tulip import qif [OPTIONS] FILE\n"))


def test_finds_usage_banner_on_stdout_too() -> None:
    assert_cli_usage_error(_result(1, stdout="Usage: tulip\n"))


def test_passes_with_matching_contains() -> None:
    assert_cli_usage_error(
        _result(2, stdout="Usage: tulip\n", stderr="Invalid value for '--account'"),
        contains="--account",
    )


def test_contains_match_is_case_insensitive() -> None:
    assert_cli_usage_error(
        _result(2, stderr="USAGE: tulip\nMissing argument BATCH_ID"),
        contains="batch_id",
    )


def test_strips_ansi_before_matching() -> None:
    # Rich wraps the banner in SGR codes; the helper must strip them so the
    # substring match doesn't break on the escape sequences.
    assert_cli_usage_error(_result(2, stderr="\x1b[1;31mUsage:\x1b[0m tulip\n"))


def test_raises_on_zero_exit() -> None:
    with pytest.raises(AssertionError):
        assert_cli_usage_error(_result(0, stderr="Usage: tulip\n"))


def test_raises_when_usage_banner_absent() -> None:
    with pytest.raises(AssertionError):
        assert_cli_usage_error(_result(1, stderr="some other failure\n"))


def test_raises_when_contains_not_found() -> None:
    with pytest.raises(AssertionError):
        assert_cli_usage_error(_result(2, stderr="Usage: tulip\n"), contains="missing-needle")
