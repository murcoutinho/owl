"""Rate-limit detection and the retry-with-backoff loop.

Ported from ``is_rate_limited`` / ``rate_limit_excerpt`` / ``retry_on_limit``
. The key subtleties preserved:

* The rate-limit regex requires HTTP-status context for ``429`` so it does
  not false-positive on code references like ``models.py:429:``.
* A successful command (rc 0) is trusted immediately — we never re-classify
  a success as rate-limited based on output prose.
* A timeout is terminal: it is NOT retried (a wedged CLI won't un-wedge, and
  retrying burns another timeout window).

``retry_on_limit`` takes an injected ``run_once`` callable and ``sleep`` so
tests exercise the backoff logic without real waits or subprocesses.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

_RATE_LIMIT_RE = re.compile(
    r"rate.?limit|too many requests|quota exceeded|overloaded|(HTTP|status|code|error)[/: ]+429\b",
    re.IGNORECASE,
)


def is_rate_limited(output: str) -> bool:
    return bool(_RATE_LIMIT_RE.search(output or ""))


def rate_limit_excerpt(output: str) -> str | None:
    """Return the first line that matches the rate-limit pattern (truncated)."""
    for line in (output or "").splitlines():
        if _RATE_LIMIT_RE.search(line):
            return line[:500]
    return None


@dataclass(frozen=True, slots=True)
class ProcResult:
    rc: int
    output: str
    timed_out: bool


def retry_on_limit(
    run_once: Callable[[], ProcResult],
    *,
    max_retries: int,
    retry_wait: int,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda _msg: None,
    desc: str = "llm",
) -> ProcResult:
    """Run ``run_once`` until it succeeds, hits a non-rate-limit error, times
    out, or exhausts ``max_retries`` rate-limit retries.

    Returns the final ``ProcResult``. The caller maps that to a higher-level
    result (success / failed / timed-out).
    """
    attempt = 0
    while True:
        attempt += 1
        if attempt > 1:
            log(f"LLM ATTEMPT: {desc} — retry {attempt}/{max_retries} starting")

        result = run_once()

        if result.timed_out:
            log(f"LLM TIMEOUT: {desc} — terminal, not retrying (exit={result.rc})")
            return result

        if result.rc == 0:
            return result

        if is_rate_limited(result.output):
            excerpt = rate_limit_excerpt(result.output) or "<no matching line found>"
            if attempt > max_retries:
                log(
                    f"LLM RATE-LIMITED: {desc} — gave up after {attempt - 1} retries "
                    f"(exit={result.rc}). Trigger: {excerpt}"
                )
                return result
            log(
                f"LLM RATE-LIMITED: {desc} — attempt {attempt}/{max_retries} "
                f"(exit={result.rc}). Trigger: {excerpt}"
            )
            log(f"LLM BACKOFF: waiting {retry_wait}s before retry")
            sleep(retry_wait)
            continue

        # A non-rate-limit failure — surface it, do not retry.
        return result
