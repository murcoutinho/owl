"""State-machine tests for the runner: resume + two-pass discovery + cycle."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from owl.runner import (
    PlanRunOutcome,
    check_plans,
    resume_pending_reviews,
    run_plan,
)
from owl.subprocess_.llm import LLMResult
from tests.conftest import FakeGh


def _ok(output: str) -> LLMResult:
    return LLMResult(rc=0, output=output, elapsed_sec=1, timed_out=False, rate_limited=False)


def _lgtm_reviewer(git_universe, repo):
    def responder(call):
        if "You are a code reviewer" in call.prompt:
            return _ok("LGTM")
        return None

    return responder


# ─── invalid plan is rejected before execution ──────────────────────────────


def test_run_plan_rejects_missing_repo(make_plan, make_deps, work_root, tmp_path):
    # A plan with no repo: field. make_plan always adds one, so write manually.
    plan_dir = tmp_path / "plan2"
    plan_dir.mkdir()
    p = plan_dir / "x.md"
    p.write_text("---\nreview-rounds: 1\n---\nbody\n")
    from owl.plans.model import Plan

    deps = make_deps()
    result = run_plan(Plan.load(p), deps, work_root=work_root)
    assert result.outcome == PlanRunOutcome.INVALID


# ─── dirty-resume plan is picked up by resume_pending_reviews ───────────────


def test_resume_continues_dirty_plan(pending_ctx, fake_llm, git_universe, make_deps, work_root):
    # First, drive a plan into DIRTY_RESUME_WAITING.
    ctx, deps = pending_ctx(review_rounds=2)

    def fail_fix(call):
        if "You are a code reviewer" in call.prompt:
            return _ok("Fix something")
        if "Apply the necessary fixes" in call.prompt:
            s = git_universe.state(ctx.worktree_repo_root)
            s.dirty = True
            s.commit_should_fail = True
            return _ok("tried")
        return None

    fake_llm.responder = fail_fix
    from owl.review.loop import ReviewOutcome, run_review_loop

    rl = run_review_loop(ctx, deps, plan_content="Do the work.")
    assert rl.outcome == ReviewOutcome.DIRTY_RESUME_WAITING
    assert ctx.work_dir.pending.exists()

    # Now resume: this time the fixer finishes and the repo can commit.
    def good_fix(call):
        if "You are a code reviewer" in call.prompt:
            return _ok("Fix something")
        if "RESUME —" in call.prompt or "Apply the necessary fixes" in call.prompt:
            s = git_universe.state(ctx.worktree_repo_root)
            s.dirty = True
            s.commit_should_fail = False
            return _ok("finished")
        return None

    fake_llm.responder = good_fix
    # Attach a gh client so the push path can run after the resume completes.
    deps = replace(deps, gh=FakeGh())

    resumed = resume_pending_reviews(deps, work_root=work_root)
    assert resumed is True


# ─── two-pass priority ordering in check_plans ──────────────────────────────


def test_check_plans_runs_normal_before_low(make_deps, git_universe, fake_llm, work_root, tmp_path):
    # Build a plan dir with one normal and one low-priority plan.
    plan_dir = tmp_path / "queue"
    plan_dir.mkdir()
    (plan_dir / "010-low.md").write_text("---\nrepo: saudade\npriority: low\nreview-rounds: 1\n---\nLOWBODY\n")
    (plan_dir / "020-normal.md").write_text("---\nrepo: saudade\nreview-rounds: 1\n---\nNORMALBODY\n")

    deps = replace(make_deps(), gh=FakeGh())

    order: list[str] = []

    def responder(call):
        if "You are a code reviewer" in call.prompt:
            return _ok("LGTM")
        # The coder/execution prompt carries the plan body ("low"/"normal").
        if "Apply the necessary fixes" not in call.prompt and "RESUME —" not in call.prompt:
            if "NORMALBODY" in call.prompt:
                order.append("normal")
            elif "LOWBODY" in call.prompt:
                order.append("low")
        return None

    fake_llm.responder = responder
    fake_llm.diff_writer = lambda cwd: setattr(
        git_universe.state(Path(cwd) / "saudade"), "dirty", True
    )

    check_plans(deps, work_root=work_root, plan_dir=plan_dir)

    # Normal-priority (020) must execute before low-priority (010) despite the
    # smaller numeric prefix on the low plan.
    assert order == ["normal", "low"]
