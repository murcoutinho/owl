"""Unit tests for owl.subprocess_.retry."""

from __future__ import annotations

from owl.subprocess_.retry import (
    ProcResult,
    is_rate_limited,
    rate_limit_excerpt,
    retry_on_limit,
)

# ─── is_rate_limited ─────────────────────────────────────────────────────────


def test_rate_limited_positive_phrases():
    assert is_rate_limited("Error: rate limit exceeded")
    assert is_rate_limited("rate-limit hit")
    assert is_rate_limited("429 too many requests")
    assert is_rate_limited("quota exceeded for this org")
    assert is_rate_limited("the server is overloaded right now")
    assert is_rate_limited("HTTP 429")
    assert is_rate_limited("status: 429")


def test_rate_limited_false_positive_guards():
    # A bare 429 in a file reference must NOT trip the detector.
    assert not is_rate_limited("models.py:429: SyntaxError")
    assert not is_rate_limited("see line 429 of the spec")
    assert not is_rate_limited("LGTM, no issues found")


def test_rate_limit_excerpt_returns_first_match_truncated():
    output = "ok line\nError: rate limit exceeded on attempt 3\nmore"
    excerpt = rate_limit_excerpt(output)
    assert excerpt is not None
    assert "rate limit exceeded" in excerpt


def test_rate_limit_excerpt_none_when_no_match():
    assert rate_limit_excerpt("all good") is None


# ─── retry_on_limit ──────────────────────────────────────────────────────────


def _ok():
    return ProcResult(rc=0, output="done", timed_out=False)


def test_retry_returns_immediately_on_success():
    calls = []

    def run_once():
        calls.append(1)
        return _ok()

    result = retry_on_limit(run_once, max_retries=2, retry_wait=300, sleep=lambda _s: None)
    assert result.rc == 0
    assert len(calls) == 1


def test_retry_does_not_retry_timeout():
    calls = []

    def run_once():
        calls.append(1)
        return ProcResult(rc=124, output="", timed_out=True)

    result = retry_on_limit(run_once, max_retries=3, retry_wait=300, sleep=lambda _s: None)
    assert result.timed_out is True
    assert len(calls) == 1  # terminal — no retry


def test_retry_does_not_retry_non_rate_limit_failure():
    calls = []

    def run_once():
        calls.append(1)
        return ProcResult(rc=1, output="some real error", timed_out=False)

    result = retry_on_limit(run_once, max_retries=3, retry_wait=300, sleep=lambda _s: None)
    assert result.rc == 1
    assert len(calls) == 1


def test_retry_backs_off_then_succeeds():
    outputs = [
        ProcResult(rc=1, output="rate limit exceeded", timed_out=False),
        ProcResult(rc=0, output="recovered", timed_out=False),
    ]
    sleeps: list[float] = []

    def run_once():
        return outputs.pop(0)

    result = retry_on_limit(
        run_once, max_retries=2, retry_wait=300, sleep=sleeps.append
    )
    assert result.rc == 0
    assert sleeps == [300]  # slept once before the successful retry


def test_retry_gives_up_after_max_retries():
    calls = []

    def run_once():
        calls.append(1)
        return ProcResult(rc=1, output="rate limit exceeded", timed_out=False)

    sleeps: list[float] = []
    result = retry_on_limit(
        run_once, max_retries=2, retry_wait=300, sleep=sleeps.append
    )
    assert result.rc == 1
    # attempt 1 (sleep), attempt 2 (sleep), attempt 3 (gives up) = 3 calls, 2 sleeps
    assert len(calls) == 3
    assert len(sleeps) == 2
