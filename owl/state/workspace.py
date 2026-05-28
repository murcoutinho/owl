"""Per-plan work directory layout and git-worktree workspace management.

``PlanWorkDir`` is the typed view over ``$WORK_DIR/<timestamp>_<slug>/`` — every
state file owl writes has a named property here, so no module ever hard-codes
a filename. ``Workspace`` owns the isolated git worktrees under
``$WORK_DIR/worktrees/<slug>/``.

The critical change from bash: ``ensure`` creates a worktree only for the
repos passed in — which, with the required single ``repo:`` field, is exactly
one. An unrelated repo is never in the workspace, so the fixer cannot dirty it
and the normalize/checkout dance can never run against it. That is the
plan-286 bug class, eliminated by construction.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..git_ops import GitLike


@dataclass(frozen=True, slots=True)
class PlanWorkDir:
    root: Path

    @classmethod
    def create(cls, work_root: Path, plan_slug: str, *, timestamp: str) -> PlanWorkDir:
        root = work_root / f"{timestamp}_{plan_slug}"
        root.mkdir(parents=True, exist_ok=True)
        return cls(root=root)

    # ── single-line markers ──────────────────────────────────────────────────
    @property
    def pending(self) -> Path:
        return self.root / "pending"

    @property
    def branch(self) -> Path:
        return self.root / "branch"

    @property
    def review_iterations(self) -> Path:
        return self.root / "review_iterations"

    @property
    def base_branch(self) -> Path:
        return self.root / "base_branch"

    @property
    def coder_session_id(self) -> Path:
        return self.root / "coder_session_id"

    @property
    def execution_project_dir(self) -> Path:
        return self.root / "execution_project_dir"

    @property
    def fix_attempts(self) -> Path:
        return self.root / "fix_attempts"

    # ── key=value markers ──────────────────────────────────────────────────
    @property
    def pending_status(self) -> Path:
        return self.root / "pending_status"

    @property
    def dirty_after_fix_failure(self) -> Path:
        return self.root / "dirty_after_fix_failure"

    @property
    def quarantined(self) -> Path:
        return self.root / "quarantined"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def resume_fix_feedback(self) -> Path:
        return self.root / ".resume_fix_feedback"

    # ── TSV manifests ──────────────────────────────────────────────────────
    @property
    def execution_base_tsv(self) -> Path:
        return self.root / "execution_base.tsv"

    @property
    def commits_tsv(self) -> Path:
        return self.root / "commits.tsv"

    @property
    def pull_requests_tsv(self) -> Path:
        return self.root / "pull_requests.tsv"

    def review_input(self, n: int) -> Path:
        return self.root / f"review_input_{n}.tsv"

    # ── prompts and logs (per iteration) ───────────────────────────────────
    @property
    def plan_prompt_wrapped(self) -> Path:
        return self.root / "plan_prompt_wrapped.txt"

    @property
    def coder_ack(self) -> Path:
        return self.root / "coder.ack"

    @property
    def execution_log(self) -> Path:
        return self.root / "execution.log"

    def fix_prompt_wrapped(self, n: int) -> Path:
        return self.root / f"fix_prompt_wrapped_{n}.txt"

    def fix_ack(self, n: int) -> Path:
        return self.root / f"fix_{n}.ack"

    def fixes_log(self, n: int) -> Path:
        return self.root / f"fixes_{n}.log"

    def combined_review(self, n: int) -> Path:
        return self.root / f"combined_review_{n}.txt"

    def reviewer_out(self, slot: int, n: int) -> Path:
        return self.root / f"reviewer{slot}_{n}.txt"

    def reviewer_ack(self, slot: int, n: int) -> Path:
        return self.root / f"reviewer{slot}_{n}.ack"

    def reviewer_prompt(self, slot: int, n: int) -> Path:
        return self.root / f"reviewer{slot}_prompt_{n}.txt"

    def tests_summary(self, n: int) -> Path:
        return self.root / f"tests_{n}.txt"

    def tests_log(self, repo_name: str, n: int) -> Path:
        return self.root / f"tests_{repo_name}_{n}.log"

    def drift_notes(self, n: int) -> Path:
        return self.root / f"drift_notes_{n}.txt"


@dataclass(frozen=True, slots=True)
class Workspace:
    """An isolated set of git worktrees for one plan.

    ``root`` is ``$WORK_DIR/worktrees/<slug>``. ``repo_roots`` maps repo name
    to its worktree path inside ``root``.
    """

    root: Path
    repo_roots: dict[str, Path]


def workspace_root_for(work_root: Path, plan_slug: str) -> Path:
    return work_root / "worktrees" / plan_slug


def ensure_workspace(
    *,
    work_root: Path,
    plan_slug: str,
    repos: dict[str, Path],
    git: Callable[[Path], GitLike],
    log: Callable[[str], None] = lambda _m: None,
) -> Workspace | None:
    """Create (or reuse) worktrees for exactly the given repos.

    ``repos`` maps repo name → source repo root. Returns a ``Workspace`` or
    ``None`` if any worktree could not be created.
    """
    root = workspace_root_for(work_root, plan_slug)
    root.mkdir(parents=True, exist_ok=True)
    repo_roots: dict[str, Path] = {}

    for repo_name, source_root in repos.items():
        worktree_root = root / repo_name
        if worktree_root.exists():
            if git(worktree_root).is_inside_work_tree():
                repo_roots[repo_name] = worktree_root
                continue
            log(f"  {repo_name}: ERROR — path exists but is not a git worktree: {worktree_root}")
            return None

        src = git(source_root)
        if not src.has_commits():
            log(f"  {repo_name}: SKIPPING — source repo has no commits")
            continue

        src.worktree_prune()
        if not src.worktree_add(worktree_root, "HEAD", detach=True):
            log(f"  {repo_name}: ERROR — failed to create worktree at {worktree_root}")
            return None
        repo_roots[repo_name] = worktree_root

    return Workspace(root=root, repo_roots=repo_roots)


def cleanup_workspace(
    workspace: Workspace,
    *,
    source_repos: dict[str, Path],
    git: Callable[[Path], GitLike],
    log: Callable[[str], None] = lambda _m: None,
) -> None:
    """Remove worktrees and delete the workspace directory."""
    for repo_name, worktree_root in workspace.repo_roots.items():
        source_root = source_repos.get(repo_name)
        if source_root is None:
            continue
        if worktree_root.exists() and not git(source_root).worktree_remove(
            worktree_root, force=True
        ):
            log(f"  {repo_name}: WARNING — failed to remove worktree {worktree_root}")
        git(source_root).worktree_prune()
    if workspace.root.exists():
        shutil.rmtree(workspace.root, ignore_errors=True)
