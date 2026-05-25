"""Unit tests for owl.cli (the argparse front-end).

End-to-end behaviour is verified through main() with captured stdout so we
exercise the same path the user does. Does not invoke real claude/codex/gh
because doctor and validate are pure relative to the environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from owl import cli


def _write_plan(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


@pytest.fixture(autouse=True)
def _clean_cwd(tmp_path: Path, monkeypatch):
    """Run each test from a clean dir so cwd-relative .env.local isn't read."""
    monkeypatch.chdir(tmp_path)
    yield


def test_validate_with_valid_plan_returns_zero(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("OWL_TARGET_REPOS", "saudade")
    p = _write_plan(tmp_path / "x.md", "---\nrepo: saudade\n---\n")
    rc = cli.main(["--validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_validate_with_missing_repo_returns_two(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("OWL_TARGET_REPOS", "saudade")
    p = _write_plan(tmp_path / "x.md", "---\nreview-rounds: 2\n---\n")
    rc = cli.main(["--validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "INVALID" in out
    assert "missing required frontmatter field 'repo'" in out


def test_validate_with_unknown_repo_returns_two(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("OWL_TARGET_REPOS", "saudade")
    p = _write_plan(tmp_path / "x.md", "---\nrepo: tucano\n---\n")
    rc = cli.main(["--validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "not in OWL_TARGET_REPOS" in out


def test_run_plan_is_not_implemented_yet(tmp_path: Path, capsys):
    p = _write_plan(tmp_path / "x.md", "---\nrepo: saudade\n---\n")
    rc = cli.main(["--run-plan", str(p)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not yet implemented" in err


def test_no_args_prints_help(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Agentic plan queue" in out


def test_mutually_exclusive_modes_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--validate", "x.md", "--doctor"])
    assert exc.value.code == 2


def test_doctor_with_no_target_repos_returns_one(monkeypatch, capsys):
    monkeypatch.delenv("OWL_TARGET_REPOS", raising=False)
    monkeypatch.setenv("OWL_SKIP_ENV_LOCAL", "1")
    rc = cli.main(["--doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "OWL_TARGET_REPOS is not set" in out
