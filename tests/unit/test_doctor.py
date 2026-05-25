"""Unit tests for owl.doctor.

Uses an injectable runner so we do not actually invoke ``gh``, ``git``, etc.
"""

from __future__ import annotations

from pathlib import Path

from owl.config import Config
from owl.doctor import format_report, run_doctor


class FakeRunner:
    """Tiny fake of subprocess.run-style invocation."""

    def __init__(self, results: dict[tuple[str, ...], tuple[int, str]]):
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        key = tuple(argv)
        self.calls.append(key)
        # Allow keying by argv prefix (e.g. ('git',) matches any git subcommand)
        for k, v in self.results.items():
            if key[: len(k)] == k:
                return v
        return 0, ""


def test_doctor_reports_missing_target_repos(tmp_path: Path):
    cfg = Config.from_env({}, project_dir=tmp_path)
    report = run_doctor(cfg, runner=FakeRunner({}))
    labels = [c.label.strip() for c in report.checks]
    assert any("OWL_TARGET_REPOS is not set" in label for label in labels)
    assert report.all_ok is False


def test_doctor_passes_when_repo_dir_exists_and_is_git(tmp_path: Path):
    repo = tmp_path / "saudade"
    repo.mkdir()
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade"},
        project_dir=tmp_path,
    )
    runner = FakeRunner(
        {
            ("gh", "auth", "status"): (0, ""),
            ("git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"): (0, "true\n"),
        }
    )
    report = run_doctor(cfg, runner=runner)
    repo_check = next(
        c for c in report.checks if c.label.strip().startswith("saudade:")
    )
    assert repo_check.ok is True


def test_doctor_reports_missing_repo_dir(tmp_path: Path):
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "ghost-repo"},
        project_dir=tmp_path,
    )
    report = run_doctor(cfg, runner=FakeRunner({}))
    repo_check = next(
        c for c in report.checks if c.label.strip().startswith("ghost-repo:")
    )
    assert repo_check.ok is False


def test_doctor_reports_non_git_dir(tmp_path: Path):
    repo = tmp_path / "saudade"
    repo.mkdir()
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade"},
        project_dir=tmp_path,
    )
    runner = FakeRunner(
        {
            ("git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"): (128, ""),
        }
    )
    report = run_doctor(cfg, runner=runner)
    repo_check = next(
        c for c in report.checks if c.label.strip().startswith("saudade:")
    )
    assert repo_check.ok is False


def test_format_report_marks_failures_clearly(tmp_path: Path):
    cfg = Config.from_env({}, project_dir=tmp_path)
    text = format_report(run_doctor(cfg, runner=FakeRunner({})))
    assert "FAIL" in text
    assert "One or more checks failed" in text
