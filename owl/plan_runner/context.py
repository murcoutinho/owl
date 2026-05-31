"""PlanContext: the resolved, in-flight state for one plan.

Bundles everything the execute step and the review loop need so they don't
re-derive paths, repo roots, or the branch name. Built once by the runner,
or rehydrated from disk on resume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..plans.model import Plan
from ..state.workspace import PlanWorkDir, Workspace


@dataclass(frozen=True, slots=True)
class PlanContext:
    plan: Plan
    work_dir: PlanWorkDir
    workspace: Workspace
    branch_name: str
    repo_name: str

    @property
    def repos(self) -> dict[str, Path]:
        """Repo name → worktree root for the repos in this plan's workspace."""
        return dict(self.workspace.repo_roots)

    @property
    def worktree_repo_root(self) -> Path:
        return self.workspace.repo_roots[self.repo_name]


def repos_for_plan(plan: Plan, cfg: Config) -> dict[str, Path]:
    """Map the plan's single declared repo to its source checkout.

    Assumes the plan has already passed validation (``repo:`` present and in
    ``cfg.target_repos``). Returns a one-entry dict.
    """
    assert plan.fm.repo is not None, "plan must declare repo: (validate first)"
    return {plan.fm.repo: cfg.project_dir / plan.fm.repo}
