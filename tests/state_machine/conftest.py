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
    def _make(*, target_repos=("saudade", "saudade-mobile", "raven")) -> Deps:
        cfg = Config.from_env(
            {"OWL_TARGET_REPOS": " ".join(target_repos)},
            project_dir=project_dir,
        )
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
