"""Run the two reviewer slots and compute the LGTM gate.

Ports the reviewer-spawn block (owl.sh:1692-1851). Each slot is claude, codex,
or none; a disabled slot contributes a synthetic ``LGTM``. Reviewers run in
parallel (threads) or sequentially per ``OWL_REVIEW_MODE``. Output is run
through ``verdict.extract_verdict`` to strip codex transcript noise, then
combined with any deterministic-test failures.

The LGTM gate (the early-exit condition) is: tests passed AND every *enabled*
reviewer's verdict is exactly ``LGTM``. Failing tests block LGTM even when both
reviewers approve — the non-negotiable rule from CONTRIBUTING.md.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..config import LLMSlot
from ..deps import Deps
from ..plan_runner.context import PlanContext
from ..state import manifest
from ..subprocess_.llm import run_slot
from ..subprocess_.prompts import write_wrapped_prompt
from .prompts import build_combined_review, build_review_prompt
from .tests import TestRunResult
from .verdict import extract_verdict, is_lgtm


@dataclass(frozen=True, slots=True)
class ReviewResult:
    combined_review: str
    tests_ok: bool
    lgtm: bool
    all_reviewers_failed: bool


def run_reviewers(
    ctx: PlanContext,
    deps: Deps,
    *,
    iteration: int,
    plan_content: str,
    tests: TestRunResult,
) -> ReviewResult:
    cfg = deps.cfg
    wd = ctx.work_dir
    entries = manifest.read_manifest(wd.review_input(iteration))
    review_prompt = build_review_prompt(plan_content, entries)

    if cfg.review_mode == "parallel" and cfg.reviewer1.enabled and cfg.reviewer2.enabled:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(
                _run_one, ctx, deps, slot=cfg.reviewer1, num=1, iteration=iteration, review_prompt=review_prompt
            )
            f2 = ex.submit(
                _run_one, ctx, deps, slot=cfg.reviewer2, num=2, iteration=iteration, review_prompt=review_prompt
            )
            r1, r2 = f1.result(), f2.result()
    else:
        r1 = _run_one(ctx, deps, slot=cfg.reviewer1, num=1, iteration=iteration, review_prompt=review_prompt)
        r2 = _run_one(ctx, deps, slot=cfg.reviewer2, num=2, iteration=iteration, review_prompt=review_prompt)

    # Both enabled reviewers failed → unrecoverable.
    enabled = [(cfg.reviewer1, r1), (cfg.reviewer2, r2)]
    failed_enabled = [r for slot, r in enabled if slot.enabled and not r.ok]
    any_enabled = any(slot.enabled for slot, _ in enabled)
    all_failed = any_enabled and len(failed_enabled) == sum(
        1 for slot, _ in enabled if slot.enabled
    )

    v1 = _verdict_for(cfg.reviewer1, r1)
    v2 = _verdict_for(cfg.reviewer2, r2)
    _log_verdict(deps.log, cfg.reviewer1, v1, r1, iteration, num=1)
    _log_verdict(deps.log, cfg.reviewer2, v2, r2, iteration, num=2)
    combined = build_combined_review(
        reviewer1_label=cfg.reviewer1.label or "Reviewer 1",
        reviewer1_verdict=v1,
        reviewer2_label=cfg.reviewer2.label or "Reviewer 2",
        reviewer2_verdict=v2,
        test_summary=tests.summary,
    )
    wd.combined_review(iteration).write_text(combined)

    lgtm = (
        tests.ok
        and (not cfg.reviewer1.enabled or is_lgtm(v1))
        and (not cfg.reviewer2.enabled or is_lgtm(v2))
    )
    return ReviewResult(
        combined_review=combined,
        tests_ok=tests.ok,
        lgtm=lgtm,
        all_reviewers_failed=all_failed,
    )


def _run_one(ctx, deps, *, slot: LLMSlot, num: int, iteration: int, review_prompt: str):
    wd = ctx.work_dir
    if not slot.enabled:
        # Synthetic LGTM, treated as a successful empty run.
        from ..subprocess_.llm import LLMResult

        wd.reviewer_out(num, iteration).write_text("LGTM")
        return LLMResult(rc=0, output="LGTM", elapsed_sec=0, timed_out=False, rate_limited=False)

    prompt_path = wd.reviewer_prompt(num, iteration)
    write_wrapped_prompt(
        prompt_path,
        ack_path=wd.reviewer_ack(num, iteration),
        base_prompt=review_prompt,
        worktree_root=ctx.workspace.root,
        project_dir=deps.cfg.project_dir,
        sub_repos=list(ctx.workspace.repo_roots.keys()),
    )
    with deps.ack_watcher(f"reviewer{num}", wd.reviewer_ack(num, iteration)):
        result = run_slot(deps.llm, slot, prompt_path, cwd=ctx.workspace.root)
    wd.reviewer_out(num, iteration).write_text(result.output)
    return result


def _verdict_for(slot: LLMSlot, result) -> str:
    if not slot.enabled:
        return "LGTM"
    return extract_verdict(result.output)


def _log_verdict(log, slot: LLMSlot, verdict: str, result, iteration: int, *, num: int) -> None:
    """Log a one-line per-reviewer summary so the verdict isn't buried in .work/."""
    label = slot.label or f"Reviewer {num}"
    if not slot.enabled:
        log(f"  [{label}] iter {iteration}: disabled (synthetic LGTM)")
        return
    if not result.ok:
        log(f"  [{label}] iter {iteration}: FAILED (exit={result.rc})")
        return
    if is_lgtm(verdict):
        log(f"  [{label}] iter {iteration}: LGTM")
        return
    # Has findings — surface the first non-empty line so it's visible at a glance.
    first_line = next(
        (line.strip() for line in verdict.splitlines() if line.strip()), ""
    )
    if len(first_line) > 140:
        first_line = first_line[:140] + "…"
    log(f"  [{label}] iter {iteration}: findings — \"{first_line}\"")
