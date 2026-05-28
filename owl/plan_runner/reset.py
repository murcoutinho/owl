"""Reset a plan's repos to their base branch before execution.

Ports ``reset_all_repos_to_base`` (owl.sh:1121-1194) and ``switch_all_to_main``.
The stale-ref guard is preserved: we require the explicit ``fetch`` to succeed
before trusting ``refs/remotes/origin/<base>``, so a cached ref from an earlier
run (when a now-merged base plan was still in flight) cannot pin a dependent
plan to yesterday's base.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..git_ops import GitLike


def reset_repos_to_base(
    *,
    repos: dict[str, Path],
    git: Callable[[Path], GitLike],
    base_branch: str | None,
    discard_owned_changes: bool = False,
    log: Callable[[str], None] = lambda _m: None,
) -> None:
    if base_branch:
        log(f"Resetting repos to base branch '{base_branch}' (fallback: default branch)...")
    else:
        log("Resetting repos to default branch...")

    for repo_name, repo_root in repos.items():
        g = git(repo_root)
        if not g.has_commits():
            continue

        if g.has_local_changes():
            if discard_owned_changes:
                log(f"  {repo_name}: discarding stale Owl-managed worktree changes before reset")
                if not g.reset_hard():
                    log(f"  {repo_name}: WARNING — failed to reset worktree")
                    continue
                if not g.clean():
                    log(f"  {repo_name}: WARNING — failed to clean worktree")
                    continue
            else:
                log(f"  {repo_name}: SKIPPING — has local changes")
                continue

        default_branch = g.default_branch()
        target_branch = default_branch
        target_ref = default_branch

        if base_branch:
            if g.fetch("origin", base_branch) and g.verify_ref(
                f"refs/remotes/origin/{base_branch}"
            ):
                target_branch = base_branch
                target_ref = f"refs/remotes/origin/{base_branch}"
                log(f"  {repo_name}: using base branch '{base_branch}' from origin")
            else:
                log(
                    f"  {repo_name}: base branch '{base_branch}' not on origin — "
                    f"falling back to {default_branch}"
                )
                g.update_ref_delete(f"refs/remotes/origin/{base_branch}")
        elif g.fetch("origin", default_branch) and g.verify_ref(
            f"refs/remotes/origin/{default_branch}"
        ):
            target_ref = f"refs/remotes/origin/{default_branch}"

        log(f"  {repo_name}: detaching worktree at '{target_branch}'")
        if not g.checkout(target_ref, detach=True):
            log(f"  {repo_name}: WARNING — failed to detach worktree at '{target_ref}'")


def switch_all_to_main(
    *,
    repos: dict[str, Path],
    git: Callable[[Path], GitLike],
    log: Callable[[str], None] = lambda _m: None,
) -> None:
    """Detach every repo back onto its default branch (neutral state)."""
    for repo_name, repo_root in repos.items():
        g = git(repo_root)
        if not g.has_commits():
            continue
        default_branch = g.default_branch()
        if not g.checkout(default_branch, detach=True):
            log(f"  {repo_name}: WARNING — failed to switch to {default_branch}")
