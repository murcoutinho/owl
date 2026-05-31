"""Run per-repo deterministic test suites before reviewers see the diff.

Ports ``run_deterministic_tests``. For each repo with an
``OWL_TEST_CMD_<repo>`` configured, run optional setup then the command in the
repo's worktree. Any failure is written to a markdown summary that gets folded
into the combined review, and the gate is: failing tests block the LGTM
early-exit (the non-negotiable rule from CONTRIBUTING.md).

The command runner is injectable so tests don't actually shell out.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# (argv-or-shell-cmd, cwd) -> (rc, combined_output)
CmdRunner = Callable[[str, Path], tuple[int, str]]


def _default_runner(cmd: str, cwd: Path) -> tuple[int, str]:
    # Operator-controlled command from .env.local — same trust boundary as
    # every other OWL_* var. Never expose to plan content or LLM output.
    proc = subprocess.run(
        cmd, shell=True, cwd=str(cwd), capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


@dataclass(frozen=True, slots=True)
class TestRunResult:
    any_configured: bool
    any_failed: bool
    summary: str

    @property
    def ok(self) -> bool:
        # No configured suites → vacuously ok. Configured + none failed → ok.
        return not self.any_failed


def run_tests(
    *,
    repos: dict[str, Path],  # repo_name -> worktree root
    test_cmd: dict[str, str],
    test_setup: dict[str, str],
    summary_path: Path,
    runner: CmdRunner = _default_runner,
    log: Callable[[str], None] = lambda _m: None,
) -> TestRunResult:
    any_configured = False
    any_failed = False
    summary_parts: list[str] = []

    for repo_name, repo_root in repos.items():
        cmd = test_cmd.get(repo_name)
        if not cmd:
            continue
        any_configured = True

        setup = test_setup.get(repo_name)
        if setup:
            log(f"[Tests] {repo_name}: setup: {setup}")
            rc, out = runner(setup, repo_root)
            if rc != 0:
                any_failed = True
                log(f"[Tests] {repo_name}: SETUP FAILED (exit={rc})")
                summary_parts.append(
                    _fail_block(repo_name, "test setup", setup, rc, out)
                )
                continue

        log(f"[Tests] {repo_name}: {cmd}")
        rc, out = runner(cmd, repo_root)
        if rc != 0:
            any_failed = True
            log(f"[Tests] {repo_name}: FAILED (exit={rc})")
            summary_parts.append(_fail_block(repo_name, "tests", cmd, rc, out))
        else:
            log(f"[Tests] {repo_name}: passed")

    summary = "\n".join(summary_parts)
    summary_path.write_text(summary)
    return TestRunResult(
        any_configured=any_configured, any_failed=any_failed, summary=summary
    )


def _fail_block(repo_name: str, what: str, cmd: str, rc: int, output: str) -> str:
    tail = "\n".join(output.splitlines()[-200:])
    return (
        f"### {repo_name} — {what} FAILED (exit={rc})\n\n"
        f"Command: `{cmd}`\n\n"
        f"```\n{tail}\n```\n"
    )
