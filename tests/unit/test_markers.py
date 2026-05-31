"""Unit tests for owl.state.markers.

These pin the byte-for-byte on-disk format. The bash implementation uses
``echo "key=value" >> file``, so a Python rewrite that ever diverges from
that format breaks resume for in-flight plans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from owl.state.markers import (
    DirtyAfterFix,
    PendingStatus,
    Quarantined,
    dump_kv,
    parse_kv,
)

# ─── parse_kv / dump_kv primitives ──────────────────────────────────────────


def test_parse_kv_basic():
    assert parse_kv("a=1\nb=hello world\n") == {"a": "1", "b": "hello world"}


def test_parse_kv_tolerates_blank_lines():
    assert parse_kv("\na=1\n\n\nb=2\n") == {"a": "1", "b": "2"}


def test_parse_kv_keeps_equals_inside_values():
    assert parse_kv("reason=cmd=git failed (rc=128)\n") == {
        "reason": "cmd=git failed (rc=128)"
    }


def test_parse_kv_ignores_lines_without_equals():
    assert parse_kv("# just a comment\nkey=val\n") == {"key": "val"}


def test_dump_kv_preserves_insertion_order():
    text = dump_kv({"z": "1", "a": "2"})
    assert text == "z=1\na=2\n"


def test_dump_kv_drops_none_values():
    text = dump_kv({"a": "1", "b": None, "c": "3"})
    assert text == "a=1\nc=3\n"


# ─── DirtyAfterFix round-trip ───────────────────────────────────────────────


def test_dirty_after_fix_round_trip(tmp_path: Path):
    p = tmp_path / "dirty_after_fix_failure"
    original = DirtyAfterFix(
        attempt=2,
        iteration=1,
        reason="normalize_all_plan_repos could not commit",
        recorded_at="2026-05-25 03:30:25",
    )
    original.write(p)
    assert (
        p.read_text()
        == "attempt=2\niteration=1\nreason=normalize_all_plan_repos could not commit\n"
        "recorded_at=2026-05-25 03:30:25\n"
    )

    loaded = DirtyAfterFix.read(p)
    assert loaded == original


def test_dirty_after_fix_read_returns_none_when_missing(tmp_path: Path):
    assert DirtyAfterFix.read(tmp_path / "nope") is None


def test_dirty_after_fix_tolerates_missing_keys():
    fm = DirtyAfterFix.from_text("reason=only-reason\n")
    assert fm.attempt == 0
    assert fm.iteration == 0
    assert fm.reason == "only-reason"
    assert fm.recorded_at == ""


def test_dirty_after_fix_tolerates_extra_unknown_keys():
    text = "attempt=1\niteration=2\nreason=x\nrecorded_at=2026\nfuture_field=ignored\n"
    fm = DirtyAfterFix.from_text(text)
    assert fm.attempt == 1
    assert fm.iteration == 2


# ─── PendingStatus round-trip ───────────────────────────────────────────────


def test_pending_status_round_trip(tmp_path: Path):
    p = tmp_path / "pending_status"
    original = PendingStatus(
        plan_name="287-server-otp.md",
        plan_file="/Users/x/plan/287-server-otp.md",
        branch_name="owl/287-server-otp",
        failed_iteration=1,
        total_iterations=2,
        reviews_done=0,
        fix_attempts=1,
        reason="dirty_after_fix_failure: fixer ran for iteration 1 but...",
        category="dirty_fix",
        aborted_at="2026-05-25 03:03:12",
    )
    original.write(p)
    loaded = PendingStatus.read(p)
    assert loaded == original


def test_pending_status_unknown_category_defaults_to_dirty_fix():
    text = "category=banana\nplan_name=x\n"
    s = PendingStatus.from_text(text)
    assert s.category == "dirty_fix"


@pytest.mark.parametrize("category", ["dirty_fix", "llm_failure"])
def test_pending_status_valid_categories_round_trip(category: str):
    s = PendingStatus.from_text(f"category={category}\nplan_name=x\n")
    assert s.category == category


# ─── Quarantined round-trip ─────────────────────────────────────────────────


def test_quarantined_round_trip(tmp_path: Path):
    p = tmp_path / "quarantined"
    original = Quarantined(
        plan_name="286-server-apple-signin-and-email-verification.md",
        plan_file="/Users/x/plan/286-server-apple-signin-and-email-verification.md",
        status="quarantined",
        reason="dirty_after_fix_failure: ...",
        fix_attempts=3,
        quarantine_file="/Users/x/plan/quarantine/286-...quarantined.md",
        quarantined_at="2026-05-25 03:57:49",
    )
    original.write(p)
    loaded = Quarantined.read(p)
    assert loaded == original


def test_quarantined_read_returns_none_when_missing(tmp_path: Path):
    assert Quarantined.read(tmp_path / "nope") is None
