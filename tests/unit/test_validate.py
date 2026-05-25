"""Unit tests for owl.validate.

These are the regression tests for the 286 bug class: a plan that does
not declare ``repo:``, or declares an unknown repo, must fail validation.
"""

from __future__ import annotations

from pathlib import Path

from owl.config import Config
from owl.validate import format_report, validate_plan


def _cfg(repos: tuple[str, ...] = ("saudade", "saudade-mobile", "raven")) -> Config:
    return Config.from_env({"OWL_TARGET_REPOS": " ".join(repos)})


# ─── happy path ──────────────────────────────────────────────────────────────


def test_valid_plan_returns_ok(tmp_path: Path):
    p = tmp_path / "001-feature.md"
    p.write_text("---\nrepo: saudade\nreview-rounds: 2\n---\nBody.\n")
    report = validate_plan(p, _cfg())
    assert report.ok is True
    assert report.repo == "saudade"
    assert report.review_rounds == 2
    assert report.error is None


# ─── the 286 bug class ───────────────────────────────────────────────────────


def test_missing_repo_field_is_invalid(tmp_path: Path):
    p = tmp_path / "002-feature.md"
    p.write_text("---\nreview-rounds: 2\n---\nBody.\n")
    report = validate_plan(p, _cfg())
    assert report.ok is False
    assert "missing required frontmatter field 'repo'" in (report.error or "")
    assert report.repo is None


def test_unknown_repo_is_invalid(tmp_path: Path):
    p = tmp_path / "003-feature.md"
    p.write_text("---\nrepo: tucano\n---\nBody.\n")
    report = validate_plan(p, _cfg())
    assert report.ok is False
    assert "not in OWL_TARGET_REPOS" in (report.error or "")


def test_no_frontmatter_at_all_is_invalid(tmp_path: Path):
    p = tmp_path / "004-feature.md"
    p.write_text("Just a body, no frontmatter.\n")
    report = validate_plan(p, _cfg())
    assert report.ok is False
    assert "missing required frontmatter field 'repo'" in (report.error or "")


# ─── auxiliary fields are surfaced even on failure ───────────────────────────


def test_failure_still_reports_other_fields(tmp_path: Path):
    p = tmp_path / "005.md"
    p.write_text(
        "---\nrepo: tucano\nreview-rounds: 2\npriority: low\nbase-branch: owl/foo\n---\n"
    )
    report = validate_plan(p, _cfg())
    assert report.ok is False
    assert report.priority == "low"
    assert report.base_branch == "owl/foo"


# ─── file-not-found ──────────────────────────────────────────────────────────


def test_missing_plan_file_is_invalid(tmp_path: Path):
    report = validate_plan(tmp_path / "nope.md", _cfg())
    assert report.ok is False
    assert "plan file not found" in (report.error or "")


# ─── format_report (sanity check the user-visible shape) ────────────────────


def test_format_report_ok(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("---\nrepo: saudade\n---\n")
    report = validate_plan(p, _cfg())
    text = format_report(report)
    assert "plan x.md: OK" in text
    assert "repo:" in text
    assert "saudade" in text


def test_format_report_invalid_includes_error(tmp_path: Path):
    p = tmp_path / "y.md"
    p.write_text("---\nreview-rounds: 2\n---\n")
    report = validate_plan(p, _cfg())
    text = format_report(report)
    assert "plan y.md: INVALID" in text
    assert "error:" in text
