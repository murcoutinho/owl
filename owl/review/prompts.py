"""Builders for the review, fix, and resume-fix prompts.

Ports the heredocs in run_review_loop  and
``build_resume_fix_prompt``. Kept as pure string builders so
they're unit-testable; the loop writes the result to disk and wraps it with
the ack/worktree contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..state.manifest import ManifestEntry

_REVIEW_INTRO = (
    "You are a code reviewer. Review the changes in the commits listed below for "
    "bugs, security issues, code quality problems, and correctness. Judge the diff "
    "against the plan below — if a change looks surprising but matches what the plan "
    "explicitly asked for, that is NOT a bug and must not be flagged. Only flag things "
    "that are wrong relative to the plan or introduce genuine defects (security, "
    "correctness, crashes, obviously broken logic). In particular, make sure that this "
    "change does not cause regression of other features — check call sites of modified "
    "functions, removed/renamed exports, changed signatures or shared types, and any "
    "feature that consumes the touched files. Be concise — return only actionable "
    "fixes, no praise. If nothing needs fixing, respond with exactly: LGTM"
)

_FIX_INTRO = """\
You received the following code review feedback on recent changes in this project. \
Apply the necessary fixes. Only change what the review asks for — do not refactor \
unrelated code.

How to handle conflicts between the plan and the reviewers:

The plan below describes the intended behavior and is the default source of truth. \
Reviewer comments should be weighed against it, not applied blindly.

1. If a reviewer comment is consistent with the plan, apply the fix.
2. If a reviewer comment CONTRADICTS the plan, do not apply it automatically. First \
reason about whether the reviewer identified a real defect (a concrete bug, crash, \
data loss, security issue, incorrect result, or broken build). If yes, deviate and \
note why. If it is a style preference or hedged question, follow the plan.
3. When in doubt, the plan wins.

IMPORTANT: Do NOT commit, push, or create branches. Just write the code fixes.\
"""

_TEST_FAILURE_PREAMBLE = (
    "These suites were run automatically by Owl after the previous commit and failed. "
    "The fix MUST make them pass again; treat this as non-negotiable, ranking higher "
    "than any reviewer preference that contradicts a failing test. Do not delete, skip, "
    "or trivially mock tests just to make them pass — fix the actual cause."
)


def build_review_prompt(plan_content: str, manifest: Iterable[ManifestEntry]) -> str:
    lines = [_REVIEW_INTRO, "", "## Plan being implemented", ""]
    lines.append(plan_content or "(plan file unavailable)")
    lines += ["", "## Commits to review", "", "Use git diff to inspect the changes:", ""]
    for e in manifest:
        if e.base_hash != "NONE" and e.base_hash:
            lines.append(
                f"- Repo: {e.repo_name} (path: {e.repo_root}) — run: "
                f"git -C {e.repo_root} diff {e.base_hash} {e.after_hash}"
            )
        else:
            lines.append(
                f"- Repo: {e.repo_name} (path: {e.repo_root}) — run: "
                f"git -C {e.repo_root} show {e.after_hash}"
            )
    return "\n".join(lines)


def build_combined_review(
    *,
    reviewer1_label: str,
    reviewer1_verdict: str,
    reviewer2_label: str,
    reviewer2_verdict: str,
    test_summary: str = "",
) -> str:
    text = (
        f"## {reviewer1_label} Review\n\n{reviewer1_verdict}\n\n"
        f"## {reviewer2_label} Review\n\n{reviewer2_verdict}"
    )
    if test_summary.strip():
        text += (
            "\n\n## Deterministic test failures\n\n"
            f"{_TEST_FAILURE_PREAMBLE}\n\n{test_summary}"
        )
    return text


def build_fix_prompt(plan_content: str, combined_review: str) -> str:
    return (
        f"{_FIX_INTRO}\n\n"
        f"## Plan being implemented\n\n{plan_content or '(plan file unavailable)'}\n\n"
        f"## Review Feedback\n\n{combined_review}\n"
    )


def build_resume_fix_prompt(
    *,
    prior_reason: str,
    dirty_files: str,
    snapshot_path: str | None,
    original_feedback: str,
) -> str:
    parts = [
        "RESUME — a previous automated fix attempt did not finish cleanly.",
        "",
        "On the previous cycle you (the fix agent) changed files in this project but "
        "the orchestrator could NOT commit your work, so it was left in the current "
        "dirty worktree. Your job now is to FINISH or fully UNDO that work.",
        "",
        "## Why the previous attempt failed",
        "",
        prior_reason or "(reason not captured)",
        "",
        "## Files you left dirty in the worktree",
        "",
        dirty_files if dirty_files.strip() else "(no dirty files detected)",
        "",
    ]
    if snapshot_path:
        parts += [f"A snapshot of those changes was saved to:\n  {snapshot_path}", ""]
    parts += [
        "## What to do now",
        "",
        "Continue from the CURRENT dirty worktree. Do exactly ONE of:",
        "1. FINISH the fix so the code is correct and deterministic tests pass.",
        "2. REVERT your own partial changes so the worktree returns to a coherent state.",
        "",
        "Do NOT commit, push, or create branches — the orchestrator commits.",
        "",
        "## Original review feedback that prompted the fix",
        "",
        original_feedback or "(original review feedback unavailable)",
    ]
    return "\n".join(parts)
