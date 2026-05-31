"""Unit tests for owl.lint_plan.

Covers the two rules from the owl-plan-author skill:
  1. Edit-target path rule in "What to change" / "Files to modify" sections.
  2. The "No plan-number references in code" sentinel must appear in the body.

Frontmatter and fenced code blocks are exempt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from owl import cli
from owl.lint_plan import format_report, lint_plan

# A minimal plan that passes both rules. Tests can prepend/append/swap sections.
_VALID_PLAN = """---
repo: saudade
---

# Plan title

## What to change
- Edit `pipeline/foo.py` to add bar.

## What does NOT change
No plan-number references in code.

## Files to modify
- pipeline/foo.py
"""


def _write(tmp_path: Path, body: str, name: str = "draft.md") -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


# ─── Rule 1: edit-target paths ──────────────────────────────────────────────


def test_valid_plan_passes(tmp_path: Path):
    p = _write(tmp_path, _VALID_PLAN)
    report = lint_plan(p)
    assert report.ok
    assert report.violations == ()
    assert not report.sentinel_missing


def test_absolute_path_in_what_to_change_is_flagged(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- Edit `pipeline/foo.py` to add bar.",
        "- Edit /Users/lana/saudade/pipeline/foo.py to add bar.",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert not report.ok
    assert len(report.violations) == 1
    assert report.violations[0].offender.startswith("/Users/lana/saudade/pipeline/foo.py")


def test_absolute_path_inside_backticks_is_flagged(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- Edit `pipeline/foo.py` to add bar.",
        "- Edit `/Users/lana/saudade/pipeline/foo.py` to add bar.",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert not report.ok
    assert len(report.violations) == 1


def test_project_root_placeholder_in_edit_section_is_flagged(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- Edit `pipeline/foo.py` to add bar.",
        "- Edit <project-root>/saudade/pipeline/foo.py to add bar.",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert not report.ok
    assert any(v.offender == "<project-root>/" for v in report.violations)


def test_absolute_path_in_files_to_modify_is_flagged(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- pipeline/foo.py",
        "- /absolute/pipeline/foo.py",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert not report.ok
    assert len(report.violations) == 1


def test_absolute_path_in_other_section_is_ignored(tmp_path: Path):
    """Absolute paths in Section 3 ('Existing files to anchor on') are fine."""
    body = (
        _VALID_PLAN
        + "\n## Existing files to anchor on\n- /Users/lana/saudade/pipeline/foo.py:120\n"
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert report.ok


def test_absolute_path_inside_code_fence_is_ignored(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- Edit `pipeline/foo.py` to add bar.",
        "```py\n# /Users/lana/saudade/pipeline/foo.py inside snippet\n```",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert report.ok


def test_absolute_path_in_frontmatter_is_ignored(tmp_path: Path):
    body = (
        "---\n"
        "repo: saudade\n"
        "anchor: /Users/lana/saudade/pipeline/foo.py\n"
        "---\n" + _VALID_PLAN.split("---\n", 2)[-1]
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert report.ok


# ─── Rule 2: sentinel ───────────────────────────────────────────────────────


def test_missing_sentinel_is_flagged(tmp_path: Path):
    body = _VALID_PLAN.replace("No plan-number references in code.", "")
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert not report.ok
    assert report.sentinel_missing


def test_sentinel_case_insensitive(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "No plan-number references in code.",
        "no plan-number references in code (verbatim).",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert report.ok


def test_sentinel_wrapped_in_quote_or_bold_passes(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "No plan-number references in code.",
        "> **No plan-number references in code.**",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    assert report.ok


# ─── Reporting ──────────────────────────────────────────────────────────────


def test_format_report_ok_message(tmp_path: Path):
    p = _write(tmp_path, _VALID_PLAN)
    report = lint_plan(p)
    out = format_report(report)
    assert out.endswith("OK (0 violations)")


def test_format_report_failure_lists_each_violation(tmp_path: Path):
    body = _VALID_PLAN.replace(
        "- Edit `pipeline/foo.py` to add bar.",
        "- Edit /Users/x/foo.py\n- Edit <project-root>/y.py",
    )
    p = _write(tmp_path, body)
    report = lint_plan(p)
    out = format_report(report)
    assert "FAIL" in out
    assert "/Users/x/foo.py" in out
    assert "<project-root>/" in out


def test_format_report_lists_sentinel_when_missing(tmp_path: Path):
    body = _VALID_PLAN.replace("No plan-number references in code.", "")
    p = _write(tmp_path, body)
    out = format_report(lint_plan(p))
    assert "Missing sentinel" in out


# ─── CLI integration ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OWL_SKIP_ENV_LOCAL", "1")
    monkeypatch.setenv("OWL_TARGET_REPOS", "saudade")
    yield


def test_cli_lint_valid_plan_exits_zero(tmp_path: Path, capsys):
    p = _write(tmp_path, _VALID_PLAN)
    rc = cli.main(["--lint", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_cli_lint_fails_with_exit_one(tmp_path: Path, capsys):
    body = _VALID_PLAN.replace(
        "- pipeline/foo.py",
        "- /abs/pipeline/foo.py",
    )
    p = _write(tmp_path, body)
    rc = cli.main(["--lint", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out


def test_cli_lint_missing_file_exits_two(tmp_path: Path, capsys):
    rc = cli.main(["--lint", str(tmp_path / "nope.md")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "file not found" in out
