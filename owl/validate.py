"""``owl --validate <plan>``: parse a plan file and report what would happen.

The validator does *not* touch git, does not call any LLM, and acquires
no locks. It is safe to run any time. Exit codes:

* ``0`` — plan is valid, would execute
* ``2`` — plan is malformed or its declared ``repo:`` is missing/unknown

The most important contract is the ``repo:`` check. Every new plan must
declare a single ``repo:`` in frontmatter, and the value must be one of
``OWL_TARGET_REPOS``. This eliminates the bug class behind plan 286:
owl will not even create a worktree for a repo the plan did not declare.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .plans.model import Plan


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of validating a plan file. ``ok`` controls the exit code."""

    ok: bool
    plan_name: str
    repo: str | None
    review_rounds: int
    priority: str
    base_branch: str | None
    error: str | None


def validate_plan(plan_file: Path, cfg: Config) -> ValidationReport:
    """Load and validate a plan against the given config."""
    try:
        plan = Plan.load(
            plan_file,
            default_review_rounds=cfg.review_iterations,
            max_review_rounds=cfg.max_review_rounds,
        )
    except FileNotFoundError:
        return ValidationReport(
            ok=False,
            plan_name=plan_file.name,
            repo=None,
            review_rounds=cfg.review_iterations,
            priority="normal",
            base_branch=None,
            error=f"plan file not found: {plan_file}",
        )

    fm = plan.fm

    if fm.repo is None:
        return _fail(
            plan,
            f"plan {plan.name}: missing required frontmatter field 'repo'. "
            f"Every plan must declare a single repo in frontmatter "
            f"(e.g. 'repo: saudade'). Allowed values: {', '.join(cfg.target_repos) or '<none configured>'}.",
        )

    if fm.repo not in cfg.target_repos:
        return _fail(
            plan,
            f"plan {plan.name}: declares 'repo: {fm.repo}' but that repo is not in "
            f"OWL_TARGET_REPOS ({', '.join(cfg.target_repos) or '<unset>'}).",
        )

    return ValidationReport(
        ok=True,
        plan_name=plan.name,
        repo=fm.repo,
        review_rounds=fm.review_rounds,
        priority=fm.priority,
        base_branch=fm.base_branch,
        error=None,
    )


def _fail(plan: Plan, msg: str) -> ValidationReport:
    return ValidationReport(
        ok=False,
        plan_name=plan.name,
        repo=plan.fm.repo,
        review_rounds=plan.fm.review_rounds,
        priority=plan.fm.priority,
        base_branch=plan.fm.base_branch,
        error=msg,
    )


def format_report(report: ValidationReport) -> str:
    """Render a report for stdout. The shape is stable so users can grep it."""
    lines: list[str] = []
    if report.ok:
        lines.append(f"plan {report.plan_name}: OK")
    else:
        lines.append(f"plan {report.plan_name}: INVALID")
        if report.error:
            lines.append(f"  error: {report.error}")
    lines.append(f"  repo:          {report.repo or '<missing>'}")
    lines.append(f"  review-rounds: {report.review_rounds}")
    lines.append(f"  priority:      {report.priority}")
    lines.append(f"  base-branch:   {report.base_branch or '<none>'}")
    return "\n".join(lines)
