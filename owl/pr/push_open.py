"""Push the plan branch and open PRs. Ports push_and_open_prs.

For each repo that has the plan branch with commits beyond its PR base: push
with upstream tracking and open a PR via gh. The PR base defaults to the repo's
default branch, or the plan's declared base branch when origin currently has it
(same stale-ref guard as reset). Returns False if any PR failed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..deps import Deps
from ..gh_ops import GhLike
from ..plan_runner.context import PlanContext
from ..state import manifest
from .body import pr_body, pr_title


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    pr_urls: dict[str, str]


def push_and_open_prs(
    ctx: PlanContext,
    deps: Deps,
    *,
    gh: GhLike,
    plan_content: str,
    rounds_completed: int,
    rounds_total: int,
    pr_base_branch: str | None = None,
) -> PushResult:
    cfg = deps.cfg
    log = deps.log
    had_failure = False
    pr_urls: dict[str, str] = {}

    for repo_name, repo_root in ctx.repos.items():
        g = deps.git(repo_root)
        if not g.verify_ref(ctx.branch_name):
            continue

        repo_pr_base = g.default_branch()
        if pr_base_branch:
            if g.fetch("origin", pr_base_branch) and g.verify_ref(
                f"refs/remotes/origin/{pr_base_branch}"
            ):
                repo_pr_base = pr_base_branch
            else:
                g.update_ref_delete(f"refs/remotes/origin/{pr_base_branch}")

        if not g.log_oneline(f"origin/{repo_pr_base}..{ctx.branch_name}"):
            g.branch_delete(ctx.branch_name, force=False)
            continue

        log(f"Pushing branch '{ctx.branch_name}' in {repo_name} (PR base: {repo_pr_base})...")
        if not g.checkout(ctx.branch_name):
            log(f"  {repo_name}: failed to checkout branch. PR creation failed.")
            had_failure = True
            continue
        if not g.push("origin", ctx.branch_name, set_upstream=True):
            log(f"  {repo_name}: push failed. PR creation failed.")
            had_failure = True
            continue

        log(f"Opening PR in {repo_name}...")
        result = gh.pr_create(
            repo_root,
            base=repo_pr_base,
            title=pr_title(cfg.pr_prefix, ctx.plan.slug),
            body=pr_body(
                plan_slug=ctx.plan.slug,
                plan_content=plan_content,
                rounds_completed=rounds_completed,
                rounds_total=rounds_total,
            ),
        )
        if result.ok:
            log(f"PR created in {repo_name}: {result.url}")
            manifest.append_pr(ctx.work_dir.pull_requests_tsv, repo_name, result.url)
            pr_urls[repo_name] = result.url
        else:
            log(f"Failed to create PR in {repo_name}: {result.error}")
            had_failure = True

    return PushResult(ok=not had_failure, pr_urls=pr_urls)
