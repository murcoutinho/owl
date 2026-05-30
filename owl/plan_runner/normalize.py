"""Commit a plan's worktree changes onto its branch, recording the manifest.

Ports ``ensure_plan_branch_checked_out``,
``normalize_repo_changes_for_plan`` and
``normalize_all_plan_repos``.

The stash-then-checkout recovery in ``ensure_branch_checked_out`` is faithfully
ported. It is now safe because, with the single ``repo:`` field, normalize only
ever iterates the plan's own repo — never an unrelated one whose branch does
not exist (the plan-286 failure).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..git_ops import GitLike
from ..state import manifest
from ..state.manifest import BaseEntry, ManifestEntry


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    committed: list[ManifestEntry]
    had_error: bool

    @property
    def ok(self) -> bool:
        return not self.had_error

    @property
    def made_a_commit(self) -> bool:
        return bool(self.committed)


def ensure_branch_checked_out(
    git: GitLike, repo_name: str, branch_name: str, *, log: Callable[[str], None]
) -> bool:
    """Ensure ``branch_name`` is checked out, stashing local changes if needed."""
    if git.current_branch() == branch_name:
        return True
    if git.checkout(branch_name):
        return True

    if not git.has_local_changes():
        log(f"  {repo_name}: ERROR — failed to checkout branch '{branch_name}'")
        return False

    stash_name = f"owl-normalize-{branch_name.replace('/', '-')}-{int(time.time())}"
    log(f"  {repo_name}: stashing local changes to move them onto '{branch_name}'")
    if not git.stash_push(stash_name):
        log(f"  {repo_name}: ERROR — failed to stash local changes before checkout")
        return False
    if not git.checkout(branch_name):
        log(f"  {repo_name}: ERROR — failed to checkout branch '{branch_name}' even after stashing")
        return False
    if git.stash_list_contains(stash_name):
        if not git.stash_apply(stash_name):
            log(f"  {repo_name}: ERROR — failed to reapply stashed changes on '{branch_name}'")
            return False
        git.stash_drop(stash_name)
    return True


def normalize_repo(
    git: GitLike,
    *,
    repo_name: str,
    repo_root: Path,
    branch_name: str,
    commit_message: str,
    base_hash: str,
    branch_mode: str,
    work_dir,  # PlanWorkDir
    log: Callable[[str], None],
) -> ManifestEntry | None:
    """Commit one repo's changes. Returns the manifest entry, or None if nothing
    was committed. Raises NormalizeError on a genuine failure."""
    if not git.has_local_changes():
        return None

    if branch_mode == "create":
        log(f"  {repo_name}: changes detected → creating branch '{branch_name}'")
        if git.verify_ref(branch_name):
            log(f"  {repo_name}: deleting stale branch '{branch_name}'")
            git.branch_delete(branch_name, force=True)
        if not git.checkout(branch_name, create=True):
            raise NormalizeError(f"{repo_name}: 'git checkout -b {branch_name}' failed")
    else:
        if not ensure_branch_checked_out(git, repo_name, branch_name, log=log):
            raise NormalizeError(f"{repo_name}: could not check out '{branch_name}'")

    if not git.has_local_changes():
        return None

    before_hash = git.head_hash() or "NONE"
    git.add_all()
    if not git.commit(commit_message):
        # Either nothing to commit (benign) or a real failure. If the worktree
        # is still dirty, it was a real failure — surface it.
        if git.has_local_changes():
            raise NormalizeError(
                f"{repo_name}: 'git commit' failed; worktree left dirty on '{branch_name}'"
            )
        return None

    after_hash = git.head_hash() or "NONE"
    log(f"  {repo_name}: committed ({git.short_head()})")
    if after_hash != before_hash:
        return ManifestEntry(
            repo_name=repo_name,
            repo_root=str(repo_root),
            base_hash=base_hash,
            after_hash=after_hash,
        )
    return None


class NormalizeError(Exception):
    """A repo had changes that could not be committed onto the plan branch."""


def normalize_all(
    *,
    repos: dict[str, Path],  # repo_name -> worktree root
    git: Callable[[Path], GitLike],
    branch_name: str,
    commit_message: str,
    work_dir,  # PlanWorkDir
    branch_mode: str = "reuse",
    log: Callable[[str], None] = lambda _m: None,
) -> NormalizeResult:
    """Normalize every repo in ``repos`` onto ``branch_name``.

    Appends committed entries to ``work_dir.review_input(...)`` is the caller's
    job; here we return them so the caller controls which manifest file gets
    the rows. We DO append to ``commits.tsv`` (audit log) as we go.
    """
    base_by_repo = {
        (e.repo_name, e.repo_root): e.base_hash
        for e in manifest.read_base_tsv(work_dir.execution_base_tsv)
    }
    committed: list[ManifestEntry] = []
    had_error = False

    for repo_name, repo_root in repos.items():
        g = git(repo_root)

        if branch_mode == "reuse" and not g.verify_ref(branch_name) and not g.has_local_changes():
            continue

        base_hash = base_by_repo.get((repo_name, str(repo_root)))
        if base_hash is None:
            base_hash = g.head_hash() or "NONE"

        try:
            entry = normalize_repo(
                g,
                repo_name=repo_name,
                repo_root=repo_root,
                branch_name=branch_name,
                commit_message=commit_message,
                base_hash=base_hash,
                branch_mode=branch_mode,
                work_dir=work_dir,
                log=log,
            )
        except NormalizeError as exc:
            log(f"  ERROR — {exc}")
            had_error = True
            continue

        if entry is not None:
            committed.append(entry)
            manifest.append_commit(
                work_dir.commits_tsv, entry.repo_name, entry.after_hash[:7]
            )

    return NormalizeResult(committed=committed, had_error=had_error)


def write_execution_base(
    *,
    repos: dict[str, Path],
    git: Callable[[Path], GitLike],
    work_dir,
) -> None:
    """Snapshot each repo's HEAD as the diff anchor (execution_base.tsv)."""
    entries = []
    for repo_name, repo_root in repos.items():
        head = git(repo_root).head_hash() or "NONE"
        entries.append(BaseEntry(repo_name=repo_name, repo_root=str(repo_root), base_hash=head))
    manifest.write_base_tsv(work_dir.execution_base_tsv, entries)
