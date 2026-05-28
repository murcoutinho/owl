"""Run a subprocess with a hard timeout, killing the whole process group.

Ports the setsid + ``kill -TERM "-$pgid"`` pattern (owl.sh:1015-1052). The
bash version wraps the LLM in ``perl … POSIX::setsid`` so the CLI and all
its descendants share a fresh process group; on timeout it kills the group
so no orphaned grandchildren linger. In Python we get the same isolation
from ``Popen(start_new_session=True)`` (which calls ``setsid``) plus
``os.killpg``.

stdin is fed from a file (the prompt), matching ``- < prompt_file`` in bash.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

from .retry import ProcResult

# Seconds to wait between SIGTERM and the SIGKILL fallback.
_SIGKILL_GRACE = 3.0


def run_with_timeout(
    argv: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    stdin_path: Path | None = None,
) -> ProcResult:
    """Run ``argv`` with a hard ``timeout``. Returns rc, combined output, timed_out.

    On timeout the process group is sent SIGTERM, then SIGKILL after a short
    grace period. ``timed_out`` is True only when the timeout actually fired.
    """
    stdin_ctx = (
        open(stdin_path, "rb")  # noqa: SIM115 — closed by the with-block below
        if stdin_path is not None
        else contextlib.nullcontext(subprocess.DEVNULL)
    )
    with stdin_ctx as stdin_f:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=stdin_f,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,  # setsid: child becomes its own process-group leader
        )

    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        # Drain whatever output was produced before the kill.
        try:
            output, _ = proc.communicate(timeout=_SIGKILL_GRACE + 2)
        except subprocess.TimeoutExpired:
            output = ""

    rc = proc.returncode if proc.returncode is not None else -1
    return ProcResult(rc=rc, output=output or "", timed_out=timed_out)


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the process group, then SIGKILL after a grace period."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    _signal_group(pgid, signal.SIGTERM)
    time.sleep(_SIGKILL_GRACE)
    if proc.poll() is None:
        _signal_group(pgid, signal.SIGKILL)


def _signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)
