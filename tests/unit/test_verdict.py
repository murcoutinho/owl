"""Unit tests for owl.review.verdict."""

from __future__ import annotations

from owl.review.verdict import extract_verdict, is_lgtm


def test_no_codex_marker_returns_trimmed_text():
    raw = "  LGTM\n  "
    assert extract_verdict(raw) == "LGTM"


def test_claude_style_multi_line_review_passes_through():
    raw = """
The diff in api/router.py:42 looks fine, but the migration is missing
an index on `user_id`. Recommend adding that.
"""
    assert "missing\nan index" in extract_verdict(raw)


def test_codex_transcript_is_stripped():
    raw = """user
review this diff

codex
This is the actual verdict.

tokens used 1234
"""
    assert extract_verdict(raw) == "This is the actual verdict."


def test_last_codex_block_wins():
    """Codex sometimes interleaves user/codex turns; only the final codex
    block carries the verdict."""
    raw = """codex
draft thinking
tokens used 100

codex
Final verdict here.

tokens used 200
"""
    assert extract_verdict(raw) == "Final verdict here."


def test_codex_block_without_tokens_line_takes_to_eof():
    raw = """codex
Verdict text, no tokens line."""
    assert extract_verdict(raw) == "Verdict text, no tokens line."


def test_is_lgtm_strict_match():
    assert is_lgtm("LGTM") is True
    assert is_lgtm("  LGTM  ") is True
    assert is_lgtm("lgtm") is True


def test_is_lgtm_rejects_lgtm_inside_paragraph():
    assert is_lgtm("The change LGTM, but consider X.") is False


def test_is_lgtm_rejects_empty():
    assert is_lgtm("") is False
    assert is_lgtm("   \n  ") is False
