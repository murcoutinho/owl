"""Proof-of-life ack-file watcher.

Each LLM prompt is wrapped (see ``owl.subprocess_.prompts``) with a directive
telling the agent to write ``alive`` to an ack file as its very first action.
This watcher polls for that file and logs once it appears, so the operator
sees the agent is alive even though its stdout is invisible until it returns.

The bash version backgrounds a polling subshell (owl.sh:317-355). Here we use
a daemon thread with a stop event, exposed as a context manager so callers
can't forget to stop it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path


class AckWatcher:
    """Context manager that watches for an ack file in a background thread."""

    def __init__(
        self,
        label: str,
        ack_path: Path,
        *,
        timeout: float = 3600.0,
        poll_interval: float = 2.0,
        log: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self.label = label
        self.ack_path = Path(ack_path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.seen = False
        # Clear any stale ack from a prior run so we only detect a fresh one.
        self.ack_path.unlink(missing_ok=True)

    def __enter__(self) -> AckWatcher:
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval + 1.0)

    def _watch(self) -> None:
        waited = 0.0
        while waited < self.timeout and not self._stop.is_set():
            if self.ack_path.exists():
                self.seen = True
                self._log(f"  [{self.label}] agent alive — wrote ack file {self.ack_path}")
                return
            # Use the event's wait as an interruptible sleep.
            if self._stop.wait(self.poll_interval):
                return
            waited += self.poll_interval
