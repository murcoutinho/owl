"""Unit tests for owl.plans.frontmatter.

These are the most critical tests in the whole suite — the rewrite is
gated on every plan declaring a single ``repo:`` value, and these tests
pin the parser's contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from owl.plans.frontmatter import Frontmatter, parse, parse_file, strip_frontmatter

# ─── happy path ──────────────────────────────────────────────────────────────


def test_parses_all_four_fields():
    text = """---
review-rounds: 3
priority: low
base-branch: owl/foo
repo: saudade
---
# body
"""
    fm = parse(text)
    assert fm == Frontmatter(
        review_rounds=3, priority="low", base_branch="owl/foo", repo="saudade"
    )


def test_accepts_underscore_aliases():
    text = """---
review_rounds: 2
base_branch: owl/bar
repo: saudade
---
"""
    fm = parse(text)
    assert fm.review_rounds == 2
    assert fm.base_branch == "owl/bar"
    assert fm.repo == "saudade"


def test_strips_quotes_around_values():
    text = """---
priority: "low"
base-branch: 'owl/quoted-name'
repo: "saudade-mobile"
---
"""
    fm = parse(text)
    assert fm.priority == "low"
    assert fm.base_branch == "owl/quoted-name"
    assert fm.repo == "saudade-mobile"


# ─── defaults and missing fields ────────────────────────────────────────────


def test_missing_frontmatter_returns_defaults():
    fm = parse("# just a markdown body\n")
    assert fm == Frontmatter(
        review_rounds=2, priority="normal", base_branch=None, repo=None
    )


def test_missing_repo_returns_none_so_validator_can_report():
    """The parser does NOT raise on missing ``repo:`` — the validator does.

    This separation keeps the parser pure and the error message
    user-facing concerns out of frontmatter parsing.
    """
    text = "---\nreview-rounds: 2\n---\nbody\n"
    fm = parse(text)
    assert fm.repo is None


def test_missing_review_rounds_falls_back_to_default():
    fm = parse("---\nrepo: saudade\n---\n", default_review_rounds=5)
    assert fm.review_rounds == 5


def test_priority_normal_is_the_default_when_unspecified():
    fm = parse("---\nrepo: saudade\n---\n")
    assert fm.priority == "normal"


# ─── clamping and validation ────────────────────────────────────────────────


def test_review_rounds_clamps_to_max():
    fm = parse(
        "---\nreview-rounds: 99\nrepo: saudade\n---\n",
        max_review_rounds=3,
    )
    assert fm.review_rounds == 3


def test_review_rounds_below_one_falls_back_to_default():
    fm = parse(
        "---\nreview-rounds: 0\nrepo: saudade\n---\n",
        default_review_rounds=2,
    )
    assert fm.review_rounds == 2


def test_review_rounds_non_integer_falls_back_to_default():
    fm = parse(
        "---\nreview-rounds: not-a-number\nrepo: saudade\n---\n",
        default_review_rounds=2,
    )
    assert fm.review_rounds == 2


def test_priority_only_low_value_is_recognized():
    fm = parse("---\npriority: high\nrepo: saudade\n---\n")
    assert fm.priority == "normal"


def test_priority_is_case_insensitive():
    fm = parse("---\npriority: LOW\nrepo: saudade\n---\n")
    assert fm.priority == "low"


def test_is_low_priority_helper():
    fm = parse("---\npriority: low\nrepo: saudade\n---\n")
    assert fm.is_low_priority is True
    fm2 = parse("---\nrepo: saudade\n---\n")
    assert fm2.is_low_priority is False


# ─── malformed input ────────────────────────────────────────────────────────


def test_unclosed_frontmatter_falls_back_to_defaults():
    text = "---\nrepo: saudade\n# never closed\n\nbody line\n"
    fm = parse(text)
    assert fm == Frontmatter(
        review_rounds=2, priority="normal", base_branch=None, repo=None
    )


def test_no_opening_fence_on_first_line_is_not_frontmatter():
    text = "\n---\nrepo: saudade\n---\n"  # blank line shifts the fence
    fm = parse(text)
    assert fm.repo is None


def test_unknown_keys_are_ignored_for_forward_compat():
    text = """---
repo: saudade
future_field: something
review-rounds: 2
---
"""
    fm = parse(text)
    assert fm.repo == "saudade"
    assert fm.review_rounds == 2


def test_blank_lines_and_comments_inside_frontmatter_are_tolerated():
    text = """---
# a comment
repo: saudade

# another comment
review-rounds: 1
---
"""
    fm = parse(text)
    assert fm.repo == "saudade"
    assert fm.review_rounds == 1


def test_crlf_line_endings_work():
    text = "---\r\nrepo: saudade\r\nreview-rounds: 2\r\n---\r\nbody\r\n"
    fm = parse(text)
    assert fm.repo == "saudade"
    assert fm.review_rounds == 2


# ─── strip_frontmatter ──────────────────────────────────────────────────────


def test_strip_frontmatter_returns_body_only():
    text = """---
repo: saudade
---
# Plan body

Some text.
"""
    body = strip_frontmatter(text)
    assert body == "# Plan body\n\nSome text.\n"


def test_strip_frontmatter_returns_text_when_no_fence():
    text = "# Plan body\n\nSome text.\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_returns_text_when_unclosed():
    text = "---\nrepo: saudade\n# never closed\nbody\n"
    assert strip_frontmatter(text) == text


# ─── parse_file ─────────────────────────────────────────────────────────────


def test_parse_file_reads_from_disk(tmp_path: Path):
    p = tmp_path / "plan.md"
    p.write_text("---\nrepo: saudade\nreview-rounds: 2\n---\nbody\n")
    fm = parse_file(p)
    assert fm.repo == "saudade"
    assert fm.review_rounds == 2


# ─── parametrized smoke for typical real-world plan filenames ───────────────


@pytest.mark.parametrize("repo_value", ["saudade", "saudade-mobile", "raven", "bem-te-vi"])
def test_repo_accepts_hyphens_in_repo_name(repo_value: str):
    text = f"---\nrepo: {repo_value}\n---\n"
    fm = parse(text)
    assert fm.repo == repo_value
