"""Top-level orchestration: run a plan end-to-end, resume pending plans, and
drive the queue cycle.

Ties together execute → review loop → push/open PRs → done file, and the
resume + two-pass discovery from check_plans. Each public
function returns a structured outcome so the CLI and tests can assert without
parsing logs.

This module does NOT start an infinite poll loop on import — ``check_plans``
runs exactly one cycle. The CLI's queue mode is what loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .deps import Deps
from .finish.done import write_done_file
from .plan_runner.context import PlanContext, repos_for_plan
from .plan_runner.execute import ExecuteOutcome, execute_plan
from .plan_runner.reset import switch_all_to_main
from .plans.model import Plan
from .review.loop import ReviewOutcome, run_review_loop
from .state.markers import PendingStatus
from .state.workspace import PlanWorkDir, cleanup_workspace, ensure_workspace
from .validate import validate_plan


class PlanRunOutcome(StrEnum):
    COMPLETED = "completed"
    COMPLETED_NO_OP = "completed_no_op"
    PENDING = "pending"  # kept for next cycle (dirty resume or llm abort)
    QUARANTINED = "quarantined"
    RETRY = "retry"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PlanRunResult:
    outcome: PlanRunOutcome
    note: str = ""


# ─── run one new plan end-to-end ────────────────────────────────────────────


def run_plan(plan: Plan, deps: Deps, *, work_root: Path) -> PlanRunResult:
    report = validate_plan(plan.path, deps.cfg)
    if not report.ok:
        deps.log(report.error or f"plan {plan.name}: invalid")
        return PlanRunResult(PlanRunOutcome.INVALID, report.error or "")

    result = execute_plan(plan, deps, work_root=work_root)
    if result.outcome == ExecuteOutcome.RETRY:
        return PlanRunResult(PlanRunOutcome.RETRY, result.note)
    if result.outcome == ExecuteOutcome.COMPLETED_NO_OP:
        ctx = result.context
        _finish(ctx, deps, reviews_successful=0, review_iterations=0, note=result.note)
        return PlanRunResult(PlanRunOutcome.COMPLETED_NO_OP, result.note)

    assert result.context is not None
    return _drive_review(result.context, deps)


# ─── drive the review loop + push/done for a pending plan ───────────────────


def _drive_review(ctx: PlanContext, deps: Deps) -> PlanRunResult:
    rl = run_review_loop(ctx, deps, plan_content=ctx.plan.body)

    if rl.outcome == ReviewOutcome.QUARANTINED:
        return PlanRunResult(PlanRunOutcome.QUARANTINED)
    if rl.outcome in (ReviewOutcome.LLM_ABORT, ReviewOutcome.DIRTY_RESUME_WAITING):
        # pending marker kept; next cycle resumes.
        return PlanRunResult(PlanRunOutcome.PENDING, rl.outcome.value)

    # READY_TO_PUSH
    from .pr.push_open import push_and_open_prs

    pr_base = None
    if ctx.work_dir.base_branch.exists():
        pr_base = ctx.work_dir.base_branch.read_text().strip() or None

    push = push_and_open_prs(
        ctx,
        deps,
        gh=deps_gh(deps),
        plan_content=ctx.plan.body,
        rounds_completed=rl.reviews_completed,
        rounds_total=rl.total_iterations,
        pr_base_branch=pr_base,
    )
    if not push.ok:
        deps.log("PR creation failed for at least one touched repo. Will retry next cycle.")
        switch_all_to_main(repos=ctx.repos, git=deps.git, log=deps.log)
        return PlanRunResult(PlanRunOutcome.RETRY, "pr creation failed")

    _finish(ctx, deps, reviews_successful=rl.reviews_completed, review_iterations=rl.total_iterations)
    return PlanRunResult(PlanRunOutcome.COMPLETED)


def _finish(ctx, deps, *, reviews_successful, review_iterations, note="") -> None:
    switch_all_to_main(repos=ctx.repos, git=deps.git, log=deps.log)
    write_done_file(
        plan_file=ctx.plan.path,
        plan_name=ctx.plan.name,
        plan_body=ctx.plan.body,
        work_dir=ctx.work_dir,
        plan_dir=ctx.plan.path.parent,
        reviews_successful=reviews_successful,
        review_iterations=review_iterations,
        now=deps.now,
        completion_note=note,
        log=deps.log,
    )
    cleanup_workspace(
        ctx.workspace,
        source_repos=repos_for_plan(ctx.plan, deps.cfg),
        git=deps.git,
        log=deps.log,
    )


def deps_gh(deps: Deps):
    """Return the configured gh client, or a real GhClient when none is set."""
    if deps.gh is not None:
        return deps.gh
    from .gh_ops import GhClient

    return GhClient()


# ─── resume pending plans ───────────────────────────────────────────────────


def rehydrate_context(pending_dir: Path, plan: Plan, deps: Deps, *, work_root: Path) -> PlanContext | None:
    work_dir = PlanWorkDir(root=pending_dir)
    branch_name = (
        work_dir.branch.read_text().strip() if work_dir.branch.exists() else f"owl/{plan.slug}"
    )
    workspace = ensure_workspace(
        work_root=work_root,
        plan_slug=plan.slug,
        repos=repos_for_plan(plan, deps.cfg),
        git=deps.git,
        log=deps.log,
    )
    if workspace is None or not workspace.repo_roots:
        return None
    return PlanContext(
        plan=plan,
        work_dir=work_dir,
        workspace=workspace,
        branch_name=branch_name,
        repo_name=plan.fm.repo,  # type: ignore[arg-type]
    )


def resume_pending_reviews(deps: Deps, *, work_root: Path) -> bool:
    """Resume every pending plan. Returns True if any plan was resumed.

    Stops the cycle (returns) when a plan aborts with category=llm_failure, but
    continues past category=dirty_fix (per-plan, should not block others).
    """
    resumed = False
    worktrees_dir = work_root / "worktrees"
    for pending_file in sorted(work_root.glob("*/pending")):
        pending_dir = pending_file.parent
        if pending_dir == worktrees_dir or worktrees_dir in pending_dir.parents:
            continue
        plan_path = Path(pending_file.read_text().strip())
        if not plan_path.exists():
            deps.log(f"Pending plan file gone: {plan_path}. Cleaning up marker.")
            pending_file.unlink(missing_ok=True)
            continue

        plan = Plan.load(
            plan_path,
            default_review_rounds=deps.cfg.review_iterations,
            max_review_rounds=deps.cfg.max_review_rounds,
        )
        work_dir = PlanWorkDir(root=pending_dir)
        if work_dir.pending_status.exists():
            deps.log(f"Resuming pending review for: {plan.name} (previously aborted mid-review)")
            work_dir.pending_status.unlink(missing_ok=True)
        else:
            deps.log(f"Resuming pending review for: {plan.name}")

        ctx = rehydrate_context(pending_dir, plan, deps, work_root=work_root)
        resumed = True
        if ctx is None:
            deps.log(f"Could not rehydrate workspace for {plan.name}; leaving pending.")
            return resumed

        result = _drive_review(ctx, deps)
        if result.outcome == PlanRunOutcome.PENDING:
            category = _pending_category(work_dir)
            if category == "llm_failure":
                deps.log(f"Plan '{plan.name}' still pending (llm_failure). Stopping cycle.")
                return resumed
            deps.log(
                f"Plan '{plan.name}' still pending (category={category}). "
                "Skipping; continuing to other plans."
            )
    return resumed


def _pending_category(work_dir: PlanWorkDir) -> str:
    ps = PendingStatus.read(work_dir.pending_status)
    return ps.category if ps else "unknown"


# ─── one queue cycle ────────────────────────────────────────────────────────


def check_plans(deps: Deps, *, work_root: Path, plan_dir: Path) -> None:
    """One cycle: resume pending plans, then run new plans (two-pass priority)."""
    deps.log(f"Checking for plans in {plan_dir}...")
    if resume_pending_reviews(deps, work_root=work_root):
        deps.log("Resumed pending reviews. Will check for new plans next cycle.")
        return

    plans = _discover_plans(plan_dir, deps)
    normal = [p for p in plans if not p.fm.is_low_priority]
    low = [p for p in plans if p.fm.is_low_priority]

    for plan in normal:
        result = run_plan(plan, deps, work_root=work_root)
        if result.outcome in (PlanRunOutcome.RETRY, PlanRunOutcome.PENDING):
            deps.log(f"Plan '{plan.name}' not complete ({result.outcome}). Stopping cycle.")
            return

    if deps.cfg.skip_low_priority:
        return
    for plan in low:
        result = run_plan(plan, deps, work_root=work_root)
        if result.outcome in (PlanRunOutcome.RETRY, PlanRunOutcome.PENDING):
            deps.log(f"Plan '{plan.name}' not complete ({result.outcome}). Stopping cycle.")
            return


def _discover_plans(plan_dir: Path, deps: Deps) -> list[Plan]:
    plans: list[Plan] = []
    for path in sorted(plan_dir.glob("*.md")):
        plans.append(
            Plan.load(
                path,
                default_review_rounds=deps.cfg.review_iterations,
                max_review_rounds=deps.cfg.max_review_rounds,
            )
        )
    return plans
