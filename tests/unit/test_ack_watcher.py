"""Unit tests for owl.subprocess_.ack_watcher."""

from __future__ import annotations

import time
from pathlib import Path

from owl.subprocess_.ack_watcher import AckWatcher


def test_watcher_detects_ack_file(tmp_path: Path):
    ack = tmp_path / "ack"
    logs: list[str] = []
    with AckWatcher("coder", ack, poll_interval=0.02, log=logs.append):
        ack.write_text("alive")
        # Give the polling thread a couple of cycles to notice.
        for _ in range(50):
            if logs:
                break
            time.sleep(0.02)
    assert any("agent alive" in line for line in logs)


def test_watcher_clears_stale_ack_on_construction(tmp_path: Path):
    ack = tmp_path / "ack"
    ack.write_text("stale")
    AckWatcher("coder", ack, poll_interval=0.02)
    assert not ack.exists()


def test_watcher_stops_cleanly_without_ack(tmp_path: Path):
    ack = tmp_path / "ack"
    logs: list[str] = []
    with AckWatcher("coder", ack, poll_interval=0.02, log=logs.append):
        pass  # exit immediately
    # No ack was ever written, so no "alive" line.
    assert not any("agent alive" in line for line in logs)
