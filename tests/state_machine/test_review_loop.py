"""State-machine tests for the review/fix loop.

Each test starts from a freshly-executed pending plan (the ``pending_ctx``
fixture) and drives run_review_loop with a FakeLLM whose responses are keyed
on prompt content (reviewer vs fix prompt), so behaviour is deterministic.
"""

from __future__ import annotations

from owl.review.loop import ReviewOutcome, run_review_loop
from owl.subprocess_.llm import LLMResult


def _ok(output: str) -> LLMResult:
    return LLMResult(rc=0, output=output, elapsed_sec=1, timed_out=False, rate_limited=False)


def _fail() -> LLMResult:
    return LLMResult(rc=1, output="rate limit exceeded", elapsed_sec=1, timed_out=False, rate_limited=True)


def _is_reviewer(prompt: str) -> bool:
    return "You are a code reviewer" in prompt


def _is_fix(prompt: str) -> bool:
    return "Apply the necessary fixes" in prompt or "RESUME —" in prompt


# ─── LGTM short-circuit ──────────────────────────────────────────────────────


def test_review_lgtm_short_circuits(pending_ctx, fake_llm, logs):
    ctx, deps = pending_ctx(review_rounds=2)
    fake_llm.responder = lambda call: _ok("LGTM") if _is_reviewer(call.prompt) else None

    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    assert result.outcome == ReviewOutcome.READY_TO_PUSH
    assert result.reviews_completed == 1  # broke after round 1
    # New log surface: per-reviewer verdict + iteration gate summary.
    assert any("iter 1: LGTM" in line for line in logs)
    assert any("all LGTM → push" in line for line in logs)


# ─── failing tests block LGTM ───────────────────────────────────────────────


def test_failing_tests_block_lgtm(pending_ctx, fake_llm, git_universe, monkeypatch):
    ctx, deps = pending_ctx(review_rounds=1)
    # Configure a failing test command for saudade.
    object.__setattr__(deps.cfg, "test_cmd", {"saudade": "pytest"})

    # Reviewers say LGTM, but the test command fails → fix phase must run.
    fix_ran = {"count": 0}

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("LGTM")
        if _is_fix(call.prompt):
            fix_ran["count"] += 1
            # Fixer makes a change so the round can commit.
            git_universe.state(ctx.worktree_repo_root).dirty = True
            return _ok("fixed the test")
        return None

    fake_llm.responder = responder
    # Make the test command "fail" via a runner monkeypatch on run_tests.
    import owl.review.tests as tests_mod

    monkeypatch.setattr(
        tests_mod, "_default_runner", lambda cmd, cwd: (1, "1 failed")
    )

    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    # Tests fail → LGTM does NOT short-circuit; fix runs in iter 1 AND in the
    # verification pass (because tests are still failing after iter 1's fix).
    assert fix_ran["count"] == 2
    assert result.outcome == ReviewOutcome.READY_TO_PUSH


# ─── fix commits, then LGTM ─────────────────────────────────────────────────


def test_fix_then_pass(pending_ctx, fake_llm, git_universe, logs):
    ctx, deps = pending_ctx(review_rounds=2)
    state = {"round": 0}

    def responder(call):
        if _is_reviewer(call.prompt):
            state["round"] += 1
            # Round 1: a finding; round 2: LGTM.
            return _ok("Fix the null check at api/x.py:10") if state["round"] == 1 else _ok("LGTM")
        if _is_fix(call.prompt):
            git_universe.state(ctx.worktree_repo_root).dirty = True
            return _ok("applied fix")
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    assert result.outcome == ReviewOutcome.READY_TO_PUSH
    # Round-1 reviewer findings surface, fix commit logs the new hash,
    # round-2 LGTM closes the loop.
    assert any('iter 1: findings — "Fix the null check' in line for line in logs)
    assert any("Fix iter 1: committed" in line for line in logs)
    assert any("iter 2: LGTM" in line for line in logs)


# ─── dirty-after-fix under cap → resume waiting ─────────────────────────────


def test_fix_dirty_under_cap_arms_resume(pending_ctx, fake_llm, git_universe):
    ctx, deps = pending_ctx(review_rounds=2)

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("Fix something")
        if _is_fix(call.prompt):
            # Fixer dirties the repo but the commit will fail.
            s = git_universe.state(ctx.worktree_repo_root)
            s.dirty = True
            s.commit_should_fail = True
            return _ok("tried to fix")
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")

    assert result.outcome == ReviewOutcome.DIRTY_RESUME_WAITING
    # Markers armed for next-cycle resume.
    assert ctx.work_dir.dirty_after_fix_failure.exists()
    assert ctx.work_dir.pending.exists()  # pending kept
    ps_text = ctx.work_dir.pending_status.read_text()
    assert "category=dirty_fix" in ps_text
    assert ctx.work_dir.fix_attempts.read_text().strip() == "1"


# ─── dirty-after-fix at cap → quarantine ────────────────────────────────────


def test_fix_dirty_at_cap_quarantines(pending_ctx, fake_llm, git_universe):
    ctx, deps = pending_ctx(review_rounds=2)
    # Pre-seed the attempt counter at cap-1 so this attempt trips the cap.
    object.__setattr__(deps.cfg, "fix_failure_cap", 2)
    ctx.work_dir.fix_attempts.write_text("1\n")

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("Fix something")
        if _is_fix(call.prompt):
            s = git_universe.state(ctx.worktree_repo_root)
            s.dirty = True
            s.commit_should_fail = True
            return _ok("tried again")
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")

    assert result.outcome == ReviewOutcome.QUARANTINED
    assert ctx.work_dir.quarantined.exists()
    assert not ctx.work_dir.pending.exists()  # pending removed
    assert not ctx.plan.path.exists()  # plan moved out of the queue
    quarantine_dir = ctx.plan.path.parent / "quarantine"
    assert any(quarantine_dir.iterdir())


# ─── resume sends the resume prompt ─────────────────────────────────────────


def test_resume_sends_resume_prompt(pending_ctx, fake_llm, git_universe):
    ctx, deps = pending_ctx(review_rounds=2)
    # Arm a prior dirty-after-fix marker as if a previous cycle left dirt.
    from owl.state.markers import DirtyAfterFix

    DirtyAfterFix(attempt=1, iteration=1, reason="prior failure", recorded_at="t").write(
        ctx.work_dir.dirty_after_fix_failure
    )

    seen = {"resume_prompt": False}

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("Fix something")
        if "RESUME —" in call.prompt:
            seen["resume_prompt"] = True
            # This time the fixer finishes cleanly and commits.
            git_universe.state(ctx.worktree_repo_root).dirty = True
            return _ok("finished the fix")
        return None

    fake_llm.responder = responder
    run_review_loop(ctx, deps, plan_content="Do the work.")
    assert seen["resume_prompt"] is True


# ─── resume + clean revert (no commit) still counts toward the cap ──────────


def test_resume_clean_revert_counts_as_failure(pending_ctx, fake_llm, git_universe):
    ctx, deps = pending_ctx(review_rounds=2)
    from owl.state.markers import DirtyAfterFix

    # Simulate one prior failed attempt: both the marker AND the cap counter.
    DirtyAfterFix(attempt=1, iteration=1, reason="prior failure", recorded_at="t").write(
        ctx.work_dir.dirty_after_fix_failure
    )
    ctx.work_dir.fix_attempts.write_text("1\n")

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("Fix something")
        if "RESUME —" in call.prompt:
            # Fixer cleanly reverts — leaves the repo clean, no commit produced.
            return _ok("reverted everything")
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    # No commit on a resume round → counts as another failed attempt.
    assert result.outcome == ReviewOutcome.DIRTY_RESUME_WAITING
    assert ctx.work_dir.fix_attempts.read_text().strip() == "2"


# ─── reviewer LLM failure → llm_abort, pending kept ─────────────────────────


def test_all_reviewers_failed_keeps_pending(pending_ctx, fake_llm):
    ctx, deps = pending_ctx(review_rounds=2)
    fake_llm.responder = lambda call: _fail() if _is_reviewer(call.prompt) else None

    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    assert result.outcome == ReviewOutcome.LLM_ABORT
    assert ctx.work_dir.pending.exists()
    assert "category=llm_failure" in ctx.work_dir.pending_status.read_text()


# ─── fix LLM failure → llm_abort ────────────────────────────────────────────


def test_verification_pass_catches_unaddressed_findings(pending_ctx, fake_llm, git_universe, logs):
    """Regression for PRs #361 and #249 shipping with unaddressed findings.

    Setup: 1 review round configured. Iter 1 reviewers flag a real defect.
    The fixer "agrees with the plan" and produces no new commit. Without the
    verification pass owl would push with the finding hanging. With the
    verification pass, owl re-reviews, sees the finding still present, and
    runs one final fix.
    """
    ctx, deps = pending_ctx(review_rounds=1)
    state = {"verification_fix_ran": False}

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("High: race condition in <something>")  # always finds something
        if _is_fix(call.prompt):
            if state["verification_fix_ran"]:
                # Second (verification) fix: commit something so loop ends clean.
                git_universe.state(ctx.worktree_repo_root).dirty = True
                return _ok("addressed it on the final pass")
            # First fix (iter 1 main loop): no commit — fixer punts.
            state["verification_fix_ran"] = True
            return _ok("plan wins; no change")
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")

    assert result.outcome == ReviewOutcome.READY_TO_PUSH
    # The verification pass ran (iter 2 = N+1), and a final fix landed a commit.
    assert any("[Verification] iter 2" in line for line in logs)
    assert any("one final fix attempt" in line for line in logs)
    assert any("Fix iter 2: committed" in line for line in logs)


def test_verification_pass_skipped_when_main_loop_lgtms(pending_ctx, fake_llm, logs):
    """If the main loop exits via LGTM, the verification pass is skipped."""
    ctx, deps = pending_ctx(review_rounds=2)
    fake_llm.responder = lambda call: _ok("LGTM") if _is_reviewer(call.prompt) else None

    run_review_loop(ctx, deps, plan_content="Do the work.")
    assert not any("[Verification]" in line for line in logs)


def test_fix_llm_failure_aborts(pending_ctx, fake_llm):
    ctx, deps = pending_ctx(review_rounds=2)

    def responder(call):
        if _is_reviewer(call.prompt):
            return _ok("Fix something")
        if _is_fix(call.prompt):
            return _fail()
        return None

    fake_llm.responder = responder
    result = run_review_loop(ctx, deps, plan_content="Do the work.")
    assert result.outcome == ReviewOutcome.LLM_ABORT
    assert "category=llm_failure" in ctx.work_dir.pending_status.read_text()
