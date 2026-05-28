"""Write the per-plan done file and clear transient state. Ports write_done_file
(owl.sh:1660-1722).

The done file archives the plan body plus an execution summary (repos changed,
PRs opened, per-round review feedback) into ``plan/done/``. After writing it,
the plan is removed from the queue and the transient state files are cleared.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..state import manifest
from ..state.workspace import PlanWorkDir


def write_done_file(
    *,
    plan_file: Path,
    plan_name: str,
    plan_body: str,
    work_dir: PlanWorkDir,
    plan_dir: Path,
    reviews_successful: int,
    review_iterations: int,
    now: Callable[[], str],
    completion_note: str = "",
    log: Callable[[str], None] = lambda _m: None,
) -> Path:
    done_dir = plan_dir / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    slug = plan_name[:-3] if plan_name.endswith(".md") else plan_name
    stamp = now().replace("-", "").replace(":", "").replace(" ", "_")
    done_path = done_dir / f"{slug}_{stamp}.done.md"

    parts = [plan_body, "", "---", "", "## Execution Summary", ""]
    parts.append(f"- **Completed:** {now()}")
    parts.append(f"- **Review rounds:** {reviews_successful} completed / {review_iterations} total")
    if completion_note:
        parts.append(f"- **Outcome:** {completion_note}")

    parts.append("- **Repos changed:**")
    commits = manifest.read_commits(work_dir.commits_tsv)
    if commits:
        parts += [f"  - `{c.repo_name}` — commit `{c.short_hash}`" for c in commits]
    else:
        parts.append("  - (none)")

    parts.append("- **Pull requests:**")
    prs = manifest.read_prs(work_dir.pull_requests_tsv)
    if prs:
        parts += [f"  - `{p.repo_name}` — {p.pr_url}" for p in prs]
    else:
        parts.append("  - (none)")
    parts.append("")

    for r in range(1, reviews_successful + 1):
        parts += [f"### Review Round {r}", ""]
        cr = work_dir.combined_review(r)
        parts.append(cr.read_text() if cr.exists() else "(skipped)")
        parts.append("")

    done_path.write_text("\n".join(parts))

    # Remove from queue and clear transient state.
    plan_file.unlink(missing_ok=True)
    for p in (
        work_dir.pending,
        work_dir.state,
        work_dir.review_iterations,
        work_dir.fix_attempts,
        work_dir.dirty_after_fix_failure,
        work_dir.resume_fix_feedback,
    ):
        p.unlink(missing_ok=True)
    log(f"Wrote done file: {done_path.name}")
    return done_path
