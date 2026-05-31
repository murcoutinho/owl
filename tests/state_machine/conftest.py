"""Shared helpers for state-machine tests.

Builds a Deps wired to the FakeGit universe and a FakeLLM, plus convenience
factories for plans and the source repo layout.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from owl.config import Config
from owl.deps import Deps
from owl.plans.model import Plan
from tests.conftest import FakeGitUniverse, FakeLLM


@pytest.fixture
def make_plan(tmp_path: Path) -> Callable[..., Plan]:
    def _make(slug: str = "001-feature", *, repo: str = "saudade", body: str = "Do the work.", extra_fm: str = "") -> Plan:
        plan_dir = tmp_path / "plan"
        plan_dir.mkdir(exist_ok=True)
        path = plan_dir / f"{slug}.md"
        fm = f"repo: {repo}\n{extra_fm}"
        path.write_text(f"---\n{fm}---\n{body}\n")
        return Plan.load(path)

    return _make


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def logs() -> list[str]:
    return []


@pytest.fixture
def make_deps(
    git_universe: FakeGitUniverse, fake_llm: FakeLLM, project_dir: Path, logs: list[str]
):
    def _make(*, target_repos=("saudade", "saudade-mobile", "raven"), env=None) -> Deps:
        base_env = {
            "OWL_TARGET_REPOS": " ".join(target_repos),
            # Sequential review keeps FakeLLM call order deterministic (FakeGit
            # is not thread-safe, and parallel reviewers would race on it).
            "OWL_REVIEW_MODE": "sequential",
        }
        if env:
            base_env.update(env)
        cfg = Config.from_env(base_env, project_dir=project_dir)
        # Register source repos in the fake universe.
        for repo in target_repos:
            git_universe.add_repo(
                project_dir / repo,
                branches={"main"},
                current="main",
                head="hash0000",
            )
        return Deps(
            cfg=cfg,
            llm=fake_llm,
            git=git_universe.client,
            now=lambda: "2026-05-28 12:00:00",
            log=logs.append,
        )

    return _make


@pytest.fixture
def work_root(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


def coder_writes(universe: FakeGitUniverse, repo_name: str):
    """diff_writer/responder side-effect: mark the worktree repo dirty."""

    def mark(cwd: Path) -> None:
        universe.state(cwd / repo_name).dirty = True

    return mark


@pytest.fixture
def pending_ctx(make_plan, make_deps, git_universe, fake_llm, work_root):
    """Run execute_plan to PENDING_AFTER_EXEC and hand back the live context.

    Returns (ctx, deps). The workspace exists, the branch is committed, and the
    pending marker is written — exactly the state run_review_loop starts from.
    """

    def _make(*, repo="saudade", review_rounds=2, target_repos=("saudade", "saudade-mobile", "raven")):
        from owl.plan_runner.execute import ExecuteOutcome, execute_plan

        plan = make_plan(repo=repo, extra_fm=f"review-rounds: {review_rounds}\n")
        deps = make_deps(target_repos=target_repos)
        fake_llm.diff_writer = coder_writes(git_universe, repo)
        result = execute_plan(plan, deps, work_root=work_root)
        assert result.outcome == ExecuteOutcome.PENDING_AFTER_EXEC
        # Clear the coder diff_writer so the review phase controls behavior.
        fake_llm.diff_writer = None
        return result.context, deps

    return _make
