"""The review/fix iteration state machine.

Ports run_review_loop (owl.sh:1796-2253, minus push/done which the runner
drives). For each iteration: run deterministic tests, run reviewers, check the
LGTM gate, and if not satisfied run the fix phase. The loop returns a
``ReviewLoopResult`` describing the terminal transition:

* READY_TO_PUSH       — reviews complete (LGTM or last round); caller pushes.
* LLM_ABORT           — reviewer or fixer hit an unrecoverable LLM error;
                        pending kept, pending_status category=llm_failure.
* DIRTY_RESUME_WAITING — fixer left the worktree uncommittable, under the cap;
                        resume marker armed, pending kept.
* QUARANTINED         — fixer failed FIX_FAILURE_CAP times; plan moved aside.

On resume the loop reads ``reviews_done`` from ``state`` and heals any manifest
drift before continuing from the next iteration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..deps import Deps
from ..plan_runner.context import PlanContext
from ..plan_runner.normalize import normalize_all
from ..plan_runner.reset import switch_all_to_main
from ..state import manifest, session
from ..state.markers import PendingStatus
from .drift import heal_manifest
from .fix import FixOutcome, run_fix_phase
from .reviewers import run_reviewers
from .tests import run_tests


class ReviewOutcome(StrEnum):
    READY_TO_PUSH = "ready_to_push"
    LLM_ABORT = "llm_abort"
    DIRTY_RESUME_WAITING = "dirty_resume_waiting"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class ReviewLoopResult:
    outcome: ReviewOutcome
    reviews_completed: int
    total_iterations: int


def run_review_loop(ctx: PlanContext, deps: Deps, *, plan_content: str) -> ReviewLoopResult:
    cfg = deps.cfg
    wd = ctx.work_dir
    log = deps.log

    coder_session_id = session.load(wd.coder_session_id)
    total_iterations = _effective_iterations(wd, cfg)

    # Make sure each repo is on the plan branch (no-op on a dirty same-branch checkout).
    for repo_root in ctx.repos.values():
        g = deps.git(repo_root)
        if g.verify_ref(ctx.branch_name):
            g.checkout(ctx.branch_name)

    reviews_done = _read_reviews_done(wd)

    resume_manifest = wd.review_input(reviews_done + 1)
    if resume_manifest.exists() and resume_manifest.read_text().strip():
        heal_manifest(resume_manifest, git=deps.git, log=log)

    reviews_completed = reviews_done

    for i in range(reviews_done + 1, total_iterations + 1):
        log("-----------------------------------------")
        log(f"[Iteration {i}/{total_iterations}] Review phase")

        manifest_path = wd.review_input(i)
        if not (manifest_path.exists() and manifest_path.read_text().strip()):
            log(f"No manifest for round {i}. Skipping.")
            continue

        tests = run_tests(
            repos=ctx.repos,
            test_cmd=dict(cfg.test_cmd),
            test_setup=dict(cfg.test_setup),
            summary_path=wd.tests_summary(i),
            log=log,
        )
        review = run_reviewers(ctx, deps, iteration=i, plan_content=plan_content, tests=tests)
        reviews_completed = i

        if review.all_reviewers_failed:
            log(f"All enabled reviewers failed for iteration {i}.")
            return _llm_abort(ctx, deps, iteration=i, total=total_iterations,
                              reviews_done=reviews_completed,
                              reason="all reviewers failed (rate-limited or tool crash)")

        if review.lgtm:
            log("All enabled reviewers say LGTM and tests passed. No fixes needed.")
            break
        if not review.tests_ok:
            log("Deterministic tests failed — fix phase will address them.")

        fix = run_fix_phase(
            ctx, deps,
            iteration=i,
            total_iterations=total_iterations,
            combined_review=review.combined_review,
            plan_content=plan_content,
            coder_session_id=coder_session_id,
            reviews_completed=reviews_completed,
        )
        if fix.outcome == FixOutcome.LLM_FAILED:
            return _llm_abort(ctx, deps, iteration=i, total=total_iterations,
                              reviews_done=reviews_completed, reason=fix.reason)
        if fix.outcome == FixOutcome.DIRTY_UNDER_CAP:
            return ReviewLoopResult(ReviewOutcome.DIRTY_RESUME_WAITING, reviews_completed, total_iterations)
        if fix.outcome == FixOutcome.QUARANTINED:
            return ReviewLoopResult(ReviewOutcome.QUARANTINED, reviews_completed, total_iterations)
        # COMMITTED → next iteration.

    # ── pre-push normalization (catches any stray uncommitted changes) ──
    pre_push_manifest = wd.review_input(reviews_completed + 1)
    norm = normalize_all(
        repos=ctx.repos,
        git=deps.git,
        branch_name=ctx.branch_name,
        commit_message=f"[owl] {ctx.plan.slug} — pre-push normalization",
        work_dir=wd,
        branch_mode="reuse",
        log=log,
    )
    for entry in norm.committed:
        manifest.append_manifest_row(pre_push_manifest, entry)
    if not norm.ok:
        log("Pre-push normalization failed. Will retry next cycle.")
        switch_all_to_main(repos=ctx.repos, git=deps.git, log=log)
        return _llm_abort(ctx, deps, iteration=total_iterations, total=total_iterations,
                          reviews_done=reviews_completed, reason="pre-push normalization failed")

    return ReviewLoopResult(ReviewOutcome.READY_TO_PUSH, reviews_completed, total_iterations)


def _llm_abort(ctx, deps, *, iteration, total, reviews_done, reason) -> ReviewLoopResult:
    wd = ctx.work_dir
    PendingStatus(
        plan_name=ctx.plan.name,
        plan_file=str(ctx.plan.path),
        branch_name=ctx.branch_name,
        failed_iteration=iteration,
        total_iterations=total,
        reviews_done=reviews_done,
        fix_attempts=0,
        reason=reason,
        category="llm_failure",
        aborted_at=deps.now(),
    ).write(wd.pending_status)
    switch_all_to_main(repos=ctx.repos, git=deps.git, log=deps.log)
    return ReviewLoopResult(ReviewOutcome.LLM_ABORT, reviews_done, total)


def _effective_iterations(wd, cfg) -> int:
    n = cfg.review_iterations
    if wd.review_iterations.exists():
        raw = wd.review_iterations.read_text().strip()
        if raw.isdigit() and int(raw) >= 1:
            n = min(int(raw), cfg.max_review_rounds)
    return n


def _read_reviews_done(wd) -> int:
    if not wd.state.exists():
        return 0
    for line in wd.state.read_text().splitlines():
        if line.startswith("reviews_done="):
            tail = line.split("=", 1)[1].strip()
            return int(tail) if tail.isdigit() else 0
    return 0
