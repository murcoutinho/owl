"""End-to-end happy path against a real git repo.

Real GitClient + real worktree creation + real commits; FakeLLM writes a known
diff on the coder call and returns LGTM on the reviewer calls; FakeGh returns a
PR URL. Asserts the full lifecycle: branch created, file committed, PR row
written, done file produced, plan removed from the queue, workspace cleaned up.

No network, no real claude/codex/gh.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from owl.config import Config
from owl.deps import Deps
from owl.git_ops import GitClient
from owl.plans.model import Plan
from owl.runner import PlanRunOutcome, run_plan
from owl.subprocess_.llm import LLMResult
from tests.conftest import FakeGh, FakeLLM

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project dir containing one real git repo 'saudade' with an origin."""
    project = tmp_path / "project"
    project.mkdir()

    origin = tmp_path / "saudade-origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    repo = project / "saudade"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return project


def test_happy_path_plan_completes(project: Path, tmp_path: Path, fake_llm: FakeLLM, fake_gh: FakeGh):
    # Plan that touches saudade.
    plan_dir = project / "plan"
    plan_dir.mkdir()
    plan_path = plan_dir / "001-add-hello.md"
    plan_path.write_text("---\nrepo: saudade\nreview-rounds: 1\n---\nAdd a hello module.\n")

    # Coder writes a real file into the saudade worktree; reviewers say LGTM.
    def responder(call):
        if "You are a code reviewer" in call.prompt:
            return LLMResult(rc=0, output="LGTM", elapsed_sec=1, timed_out=False, rate_limited=False)
        return None

    def diff_writer(cwd: Path) -> None:
        (Path(cwd) / "saudade" / "hello.py").write_text("print('hello')\n")

    fake_llm.responder = responder
    fake_llm.diff_writer = diff_writer

    logs: list[str] = []
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade", "OWL_REVIEW_MODE": "sequential"},
        project_dir=project,
    )
    deps = Deps(
        cfg=cfg,
        llm=fake_llm,
        git=lambda root: GitClient(root),
        now=lambda: "2026-05-28 12:00:00",
        log=logs.append,
        gh=fake_gh,
    )

    work_root = tmp_path / "work"
    work_root.mkdir()
    plan = Plan.load(plan_path, default_review_rounds=1, max_review_rounds=3)

    result = run_plan(plan, deps, work_root=work_root)

    assert result.outcome == PlanRunOutcome.COMPLETED

    # A PR was opened against main.
    assert len(fake_gh.created) == 1
    assert fake_gh.created[0].base == "main"
    assert fake_gh.created[0].title == "[owl] 001-add-hello"

    # The branch was pushed to origin with the new file committed.
    origin = tmp_path / "saudade-origin.git"
    branches = subprocess.run(
        ["git", "-C", str(origin), "branch", "--list", "owl/001-add-hello"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "owl/001-add-hello" in branches

    # A done file was written and the plan removed from the queue.
    done_files = list((plan_dir / "done").glob("001-add-hello_*.done.md"))
    assert len(done_files) == 1
    assert "hello.py" not in plan_path.name  # sanity
    assert not plan_path.exists()  # plan consumed

    # Workspace cleaned up.
    assert not (work_root / "worktrees" / "001-add-hello").exists()


def test_no_op_plan_completes_without_pr(project: Path, tmp_path: Path, fake_llm: FakeLLM, fake_gh: FakeGh):
    plan_dir = project / "plan"
    plan_dir.mkdir()
    plan_path = plan_dir / "002-noop.md"
    plan_path.write_text("---\nrepo: saudade\n---\nWork already done.\n")

    # Coder makes no file changes.
    fake_llm.script = [LLMResult(rc=0, output="nothing to do", elapsed_sec=1, timed_out=False, rate_limited=False)]

    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade", "OWL_REVIEW_MODE": "sequential"},
        project_dir=project,
    )
    deps = Deps(cfg=cfg, llm=fake_llm, git=lambda root: GitClient(root),
                now=lambda: "2026-05-28 12:00:00", log=lambda _m: None, gh=fake_gh)
    work_root = tmp_path / "work"
    work_root.mkdir()
    plan = Plan.load(plan_path)

    result = run_plan(plan, deps, work_root=work_root)
    assert result.outcome == PlanRunOutcome.COMPLETED_NO_OP
    assert len(fake_gh.created) == 0
    assert list((plan_dir / "done").glob("002-noop_*.done.md"))
