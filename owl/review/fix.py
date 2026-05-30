"""The fix phase, including dirty-after-fix recovery and quarantine.

Ports the fix block of run_review_loop plus
``capture_dirty_snapshot``. This is the function that plan
286 stress-tested: when the fixer leaves the worktree uncommittable, owl must
preserve the dirt, arm a resume prompt, and after FIX_FAILURE_CAP attempts
quarantine the plan rather than loop forever.

Returns a ``FixOutcome`` so the loop can decide the next transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..deps import Deps
from ..plan_runner.context import PlanContext
from ..plan_runner.normalize import normalize_all
from ..state import manifest
from ..state.markers import DirtyAfterFix, PendingStatus
from ..subprocess_.llm import run_slot
from ..subprocess_.prompts import write_wrapped_prompt
from .prompts import build_fix_prompt, build_resume_fix_prompt


class FixOutcome(StrEnum):
    COMMITTED = "committed"
    LLM_FAILED = "llm_failed"
    DIRTY_UNDER_CAP = "dirty_under_cap"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class FixResult:
    outcome: FixOutcome
    reason: str = ""


def run_fix_phase(
    ctx: PlanContext,
    deps: Deps,
    *,
    iteration: int,
    total_iterations: int,
    combined_review: str,
    plan_content: str,
    coder_session_id: str | None,
    reviews_completed: int,
) -> FixResult:
    cfg = deps.cfg
    wd = ctx.work_dir
    log = deps.log
    log(f"[Iteration {iteration}/{total_iterations}] Fix phase")

    resume_fix = wd.dirty_after_fix_failure.exists()
    fix_prompt_path = wd.fix_prompt_wrapped(iteration)

    if resume_fix:
        prior = DirtyAfterFix.read(wd.dirty_after_fix_failure)
        wd.resume_fix_feedback.write_text(combined_review)
        dirty_files = capture_dirty_snapshot(ctx, deps, attempt_tag=f"resume_{iteration}")
        snapshot = wd.root / f"dirty_snapshot_resume_{iteration}.patch"
        log(f"Fix phase resuming from a prior dirty-after-fix failure (attempt {prior.attempt if prior else 0}).")
        base_prompt = build_resume_fix_prompt(
            prior_reason=prior.reason if prior else "",
            dirty_files=dirty_files,
            snapshot_path=str(snapshot) if snapshot.exists() else None,
            original_feedback=combined_review,
        )
    else:
        base_prompt = build_fix_prompt(plan_content, combined_review)

    write_wrapped_prompt(
        fix_prompt_path,
        ack_path=wd.fix_ack(iteration),
        base_prompt=base_prompt,
        worktree_root=ctx.workspace.root,
        project_dir=cfg.project_dir,
        sub_repos=list(ctx.workspace.repo_roots.keys()),
    )

    log(f"Applying fixes via {cfg.fix.provider} ({cfg.fix.model})...")
    use_resume_session = cfg.fix.provider == "claude" and coder_session_id
    with deps.ack_watcher("fix-agent", wd.fix_ack(iteration)):
        result = run_slot(
            deps.llm,
            cfg.fix,
            fix_prompt_path,
            session_id=coder_session_id if use_resume_session else None,
            session_mode="resume" if use_resume_session else "create",
            cwd=ctx.workspace.root,
        )
    wd.fixes_log(iteration).write_text(result.output)

    if not result.ok:
        log(f"Fix LLM failed for iteration {iteration} (exit={result.rc}).")
        return FixResult(FixOutcome.LLM_FAILED, "fix LLM failed (rate-limited or tool crash)")

    # Commit the fixer's work onto the plan branch.
    next_manifest = wd.review_input(iteration + 1)
    _copy_manifest(wd.review_input(iteration), next_manifest)
    before = len(manifest.read_manifest(next_manifest))

    norm = normalize_all(
        repos=ctx.repos,
        git=deps.git,
        branch_name=ctx.branch_name,
        commit_message=f"[owl] {ctx.plan.slug} — review fix iteration {iteration}",
        work_dir=wd,
        branch_mode="reuse",
        log=log,
    )
    for entry in norm.committed:
        manifest.append_manifest_row(next_manifest, entry)
    committed_a_fix = len(manifest.read_manifest(next_manifest)) > before

    if not norm.ok or (resume_fix and not committed_a_fix):
        return _handle_dirty(
            ctx, deps,
            iteration=iteration,
            total_iterations=total_iterations,
            resume_fix=resume_fix,
            normalize_ok=norm.ok,
            reviews_completed=reviews_completed,
        )

    # Success: clear resume markers, record progress.
    wd.dirty_after_fix_failure.unlink(missing_ok=True)
    wd.resume_fix_feedback.unlink(missing_ok=True)
    wd.state.write_text(f"reviews_done={iteration}\n")
    if norm.committed:
        hashes = ", ".join(e.after_hash[:7] for e in norm.committed)
        log(f"  Fix iter {iteration}: committed ({hashes})")
    else:
        log(f"  Fix iter {iteration}: no new commit (nothing to commit)")
    return FixResult(FixOutcome.COMMITTED)


def _handle_dirty(
    ctx: PlanContext,
    deps: Deps,
    *,
    iteration: int,
    total_iterations: int,
    resume_fix: bool,
    normalize_ok: bool,
    reviews_completed: int,
) -> FixResult:
    from ..finish.quarantine import quarantine_plan

    cfg = deps.cfg
    wd = ctx.work_dir
    log = deps.log

    if not normalize_ok:
        reason = (
            f"dirty_after_fix_failure: fixer ran for iteration {iteration} but "
            "normalize_all_plan_repos could not commit — worktree left dirty or uncommittable"
        )
    else:
        reason = (
            f"dirty_after_fix_failure: resume attempt for iteration {iteration} produced no "
            "commit — the fixer cleanly reverted its own partial changes instead of finishing"
        )

    attempts = _bump_fix_attempts(wd.fix_attempts)
    log(f"Fix phase failed to land a commit (attempt {attempts} of {cfg.fix_failure_cap}): {reason}")
    capture_dirty_snapshot(ctx, deps, attempt_tag=str(attempts))

    if attempts >= cfg.fix_failure_cap:
        wd.dirty_after_fix_failure.unlink(missing_ok=True)
        quarantine_plan(
            plan_file=ctx.plan.path,
            plan_name=ctx.plan.name,
            work_dir=wd,
            plan_dir=ctx.plan.path.parent,
            reason=reason,
            attempts=attempts,
            fix_failure_cap=cfg.fix_failure_cap,
            now=deps.now,
            log=log,
        )
        return FixResult(FixOutcome.QUARANTINED, reason)

    DirtyAfterFix(
        attempt=attempts, iteration=iteration, reason=reason, recorded_at=deps.now()
    ).write(wd.dirty_after_fix_failure)
    PendingStatus(
        plan_name=ctx.plan.name,
        plan_file=str(ctx.plan.path),
        branch_name=ctx.branch_name,
        failed_iteration=iteration,
        total_iterations=total_iterations,
        reviews_done=reviews_completed,
        fix_attempts=attempts,
        reason=reason,
        category="dirty_fix",
        aborted_at=deps.now(),
    ).write(wd.pending_status)
    log("Fix phase left repos dirty. Worktree changes PRESERVED for resume; pending marker kept.")
    # Intentionally NOT switching to main — the dirty changes must stay on the
    # plan branch's worktree so the next cycle can resume from them.
    return FixResult(FixOutcome.DIRTY_UNDER_CAP, reason)


def capture_dirty_snapshot(ctx: PlanContext, deps: Deps, *, attempt_tag: str) -> str:
    """Write a combined diff+untracked snapshot. Returns the dirty-files list.
    Leaves the dirt in place (read-only)."""
    snapshot = ctx.work_dir.root / f"dirty_snapshot_{attempt_tag}.patch"
    parts: list[str] = []
    dirty_files: list[str] = []
    for repo_name, repo_root in ctx.repos.items():
        g = deps.git(repo_root)
        if not g.has_local_changes():
            continue
        parts.append(f"### repo: {repo_name} ({repo_root})")
        parts.append("## tracked changes (git diff HEAD):")
        parts.append("\n".join(g.diff_name_only("HEAD")))
        parts.append("## untracked files:")
        parts.append("\n".join(g.untracked_files()))
        parts.append("")
        for path in g.diff_name_only("HEAD"):
            dirty_files.append(f"{repo_name}: {path}")
        for path in g.untracked_files():
            dirty_files.append(f"{repo_name}: {path} (untracked)")
    snapshot.write_text("\n".join(parts))
    return "\n".join(dirty_files)


def _bump_fix_attempts(path: Path) -> int:
    n = 0
    if path.exists():
        digits = "".join(c for c in path.read_text() if c.isdigit())
        n = int(digits) if digits else 0
    n += 1
    path.write_text(f"{n}\n")
    return n


def _copy_manifest(src: Path, dst: Path) -> None:
    dst.write_text(src.read_text() if src.exists() else "")
