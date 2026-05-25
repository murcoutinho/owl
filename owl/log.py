"""Timestamped logging to stdout and an optional log file.

Mirrors the ``log()`` shell function (owl.sh:223-225) — every message gets
the ``[YYYY-MM-DD HH:MM:SS]`` prefix and is duplicated to both stdout and
``$LOG_FILE`` when one is configured.

Logs are intentionally line-oriented and plain text (no JSON, no levels).
The CLI is the user — they read the log directly.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import IO

_log_file: Path | None = None


def configure(log_file: Path | None) -> None:
    """Set the log file destination. Pass ``None`` to disable file logging."""
    global _log_file
    _log_file = log_file


def log(message: str, *, stream: IO[str] = sys.stdout) -> None:
    """Write ``[timestamp] message`` to stdout and (if configured) the log file."""
    line = f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, file=stream)
    if _log_file is not None:
        with _log_file.open("a") as f:
            f.write(line + "\n")
