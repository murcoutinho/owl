"""Unit tests for owl.subprocess_.llm (command construction + runner wiring)."""

from __future__ import annotations

from pathlib import Path

import pytest

from owl.config import LLMSlot
from owl.subprocess_.llm import LLMRunner, build_argv, run_slot
from owl.subprocess_.retry import ProcResult

# ─── build_argv ──────────────────────────────────────────────────────────────


def test_build_argv_claude_no_session():
    argv = build_argv("claude", "claude-sonnet-4-6", session_id=None, session_mode="create")
    assert argv == [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "claude-sonnet-4-6",
        "-",
    ]


def test_build_argv_claude_create_session():
    argv = build_argv("claude", "m", session_id="sid-123", session_mode="create")
    assert "--session-id" in argv
    assert "sid-123" in argv
    assert "--resume" not in argv
    assert argv[-1] == "-"


def test_build_argv_claude_resume_session():
    argv = build_argv("claude", "m", session_id="sid-123", session_mode="resume")
    assert "--resume" in argv
    assert "--session-id" not in argv


def test_build_argv_codex():
    argv = build_argv("codex", "gpt-x", session_id=None, session_mode="create")
    assert argv == [
        "codex",
        "exec",
        "--full-auto",
        "--skip-git-repo-check",
        "--model",
        "gpt-x",
        "-",
    ]


def test_build_argv_unknown_provider_raises():
    with pytest.raises(ValueError, match="invalid provider"):
        build_argv("openai", "m", session_id=None, session_mode="create")


# ─── LLMRunner ───────────────────────────────────────────────────────────────


def _runner(executor, sleeps=None):
    clock = iter([100.0, 105.0, 110.0, 115.0])
    return LLMRunner(
        timeout=2400,
        max_retries=2,
        retry_wait=300,
        executor=executor,
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        clock=lambda: next(clock),
    )


def test_runner_none_provider_short_circuits(tmp_path: Path):
    called = []
    runner = _runner(lambda *a, **k: called.append(1) or ProcResult(0, "", False))
    result = runner.run("none", "x", tmp_path / "p.txt")
    assert result.ok is True
    assert called == []  # executor never invoked


def test_runner_success_path(tmp_path: Path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("hi")
    seen = {}

    def executor(argv, *, timeout, cwd, stdin_path):
        seen["argv"] = argv
        seen["stdin"] = stdin_path
        return ProcResult(rc=0, output="all good", timed_out=False)

    runner = _runner(executor)
    result = runner.run("claude", "m", prompt, session_id="sid", session_mode="create")
    assert result.ok is True
    assert result.output == "all good"
    assert seen["argv"][0] == "claude"
    assert seen["stdin"] == prompt


def test_runner_timeout_maps_to_124(tmp_path: Path):
    runner = _runner(lambda *a, **k: ProcResult(rc=137, output="", timed_out=True))
    result = runner.run("claude", "m", tmp_path / "p.txt")
    assert result.timed_out is True
    assert result.rc == 124
    assert result.ok is False


def test_runner_rate_limit_then_success(tmp_path: Path):
    results = [
        ProcResult(rc=1, output="rate limit exceeded", timed_out=False),
        ProcResult(rc=0, output="ok", timed_out=False),
    ]
    sleeps: list[float] = []
    runner = _runner(lambda *a, **k: results.pop(0), sleeps=sleeps)
    result = runner.run("codex", "m", tmp_path / "p.txt")
    assert result.ok is True
    assert sleeps == [300]


def test_runner_flags_rate_limited_on_final_failure(tmp_path: Path):
    runner = _runner(
        lambda *a, **k: ProcResult(rc=1, output="quota exceeded", timed_out=False)
    )
    result = runner.run("claude", "m", tmp_path / "p.txt")
    assert result.ok is False
    assert result.rate_limited is True


# ─── run_slot ────────────────────────────────────────────────────────────────


def test_run_slot_disabled_returns_empty_success(tmp_path: Path):
    runner = _runner(lambda *a, **k: ProcResult(0, "should not run", False))
    slot = LLMSlot(provider="none", model="x")
    result = run_slot(runner, slot, tmp_path / "p.txt")
    assert result.ok is True
    assert result.output == ""
