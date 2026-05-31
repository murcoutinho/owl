"""Integration tests for owl.subprocess_.timeout against real processes.

These spawn real subprocesses to verify the setsid + process-group kill
works on this platform (macOS is the deployment target, and threads +
signals + process groups are finicky there — see the plan's risk section).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from owl.subprocess_.timeout import run_with_timeout

pytestmark = pytest.mark.integration


def test_fast_command_returns_output(tmp_path: Path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("ignored")
    result = run_with_timeout(
        [sys.executable, "-c", "print('hello from child')"],
        timeout=10,
        stdin_path=prompt,
    )
    assert result.rc == 0
    assert result.timed_out is False
    assert "hello from child" in result.output


def test_nonzero_exit_is_captured(tmp_path: Path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("")
    result = run_with_timeout(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout=10,
        stdin_path=prompt,
    )
    assert result.rc == 3
    assert result.timed_out is False
    assert "boom" in result.output


def test_timeout_kills_the_process_group(tmp_path: Path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("")
    start = time.monotonic()
    result = run_with_timeout(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
        stdin_path=prompt,
    )
    elapsed = time.monotonic() - start
    assert result.timed_out is True
    # Should return shortly after the 1s timeout + ~3s SIGKILL grace, not 30s.
    assert elapsed < 10


def test_stdin_is_fed_from_prompt_file(tmp_path: Path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("line-from-stdin")
    result = run_with_timeout(
        [sys.executable, "-c", "import sys; print('got:', sys.stdin.read())"],
        timeout=10,
        stdin_path=prompt,
    )
    assert "got: line-from-stdin" in result.output
