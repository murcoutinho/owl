"""Dispatch a prompt to a coding/review LLM (claude, codex, or none).

Ports ``run_llm`` (owl.sh:864-925). Command lines, verbatim from bash:

* claude (resume):  claude --print --dangerously-skip-permissions --model M --resume SID -
* claude (create):  claude --print --dangerously-skip-permissions --model M --session-id SID -
* claude (no sess): claude --print --dangerously-skip-permissions --model M -
* codex:            codex exec --full-auto --skip-git-repo-check --model M -

In every case the prompt file is fed on stdin (``- < prompt_file``).

``LLMRunner`` composes the retry-with-backoff loop (``retry.retry_on_limit``)
around the timeout-protected executor (``timeout.run_with_timeout``). Both
the executor and ``sleep`` are injectable so the runner is fully testable
without real subprocesses or real waits.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import LLMSlot
from .retry import ProcResult, is_rate_limited, retry_on_limit
from .timeout import run_with_timeout


@dataclass(frozen=True, slots=True)
class LLMResult:
    rc: int
    output: str
    elapsed_sec: int
    timed_out: bool
    rate_limited: bool

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


def build_argv(provider: str, model: str, *, session_id: str | None, session_mode: str) -> list[str]:
    """Return the argv for a provider. Raises ValueError for unknown providers."""
    if provider == "claude":
        argv = ["claude", "--print", "--dangerously-skip-permissions", "--model", model]
        if session_id and session_mode == "resume":
            argv += ["--resume", session_id]
        elif session_id:
            argv += ["--session-id", session_id]
        argv.append("-")
        return argv
    if provider == "codex":
        return ["codex", "exec", "--full-auto", "--skip-git-repo-check", "--model", model, "-"]
    raise ValueError(f"invalid provider '{provider}' (expected claude, codex, or none)")


class LLMRunner:
    def __init__(
        self,
        *,
        timeout: int,
        max_retries: int,
        retry_wait: int,
        executor: Callable[..., ProcResult] = run_with_timeout,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._executor = executor
        self._sleep = sleep
        self._clock = clock
        self._log = log

    def run(
        self,
        provider: str,
        model: str,
        prompt_path: Path,
        *,
        session_id: str | None = None,
        session_mode: str = "create",
        cwd: Path | None = None,
    ) -> LLMResult:
        provider = provider.strip().lower()
        if provider == "none":
            return LLMResult(rc=0, output="", elapsed_sec=0, timed_out=False, rate_limited=False)

        argv = build_argv(provider, model, session_id=session_id, session_mode=session_mode)
        tag = f"{provider} {model} [{Path(prompt_path).name}]"
        if session_id:
            tag += f" session={session_id[:8]}({session_mode})"
        self._log(f"LLM START: {tag}")

        start = self._clock()
        result = retry_on_limit(
            lambda: self._executor(
                argv, timeout=self._timeout, cwd=cwd, stdin_path=Path(prompt_path)
            ),
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
            sleep=self._sleep,
            log=self._log,
            desc=tag,
        )
        elapsed = int(self._clock() - start)

        rate_limited = result.rc != 0 and not result.timed_out and is_rate_limited(result.output)
        outcome = LLMResult(
            rc=124 if result.timed_out else result.rc,
            output=result.output,
            elapsed_sec=elapsed,
            timed_out=result.timed_out,
            rate_limited=rate_limited,
        )
        if outcome.ok:
            self._log(f"LLM DONE: {tag} — success, {elapsed}s, {len(result.output)} chars")
        else:
            self._log(f"LLM FAILED: {tag} — exit={outcome.rc}, {elapsed}s")
        return outcome


def run_slot(
    runner: LLMRunner,
    slot: LLMSlot,
    prompt_path: Path,
    *,
    session_id: str | None = None,
    session_mode: str = "create",
    cwd: Path | None = None,
) -> LLMResult:
    """Convenience: run an ``LLMSlot``. A disabled slot returns an empty success."""
    if not slot.enabled:
        return LLMResult(rc=0, output="", elapsed_sec=0, timed_out=False, rate_limited=False)
    return runner.run(
        slot.provider,
        slot.model,
        prompt_path,
        session_id=session_id,
        session_mode=session_mode,
        cwd=cwd,
    )
