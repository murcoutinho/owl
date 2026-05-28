"""Quarantine a plan that failed the fix phase FIX_FAILURE_CAP times.

Ports ``quarantine_plan`` (owl.sh:916-970). Moves the plan ``.md`` into
``plan/quarantine/`` (mirroring ``plan/done/``) so the queue never re-picks it,
removes the ``pending`` marker so resume skips it, and writes a ``quarantined``
metadata marker. The caller returns 0 so the queue keeps draining.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..state.markers import Quarantined
from ..state.workspace import PlanWorkDir


def quarantine_plan(
    *,
    plan_file: Path,
    plan_name: str,
    work_dir: PlanWorkDir,
    plan_dir: Path,
    reason: str,
    attempts: int,
    fix_failure_cap: int,
    now: Callable[[], str],
    log: Callable[[str], None] = lambda _m: None,
) -> Quarantined:
    quarantine_dir = plan_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    stamp = now().replace("-", "").replace(":", "").replace(" ", "_")
    slug = plan_name[:-3] if plan_name.endswith(".md") else plan_name
    quarantine_path = quarantine_dir / f"{slug}_{stamp}.quarantined.md"

    header = (
        "# QUARANTINED by Owl\n#\n"
        f"# This plan failed the fix phase {attempts} time(s) (cap {fix_failure_cap})\n"
        "# and was quarantined so it would not block the queue.\n#\n"
        f"# reason: {reason}\n"
        f"# quarantined_at: {now()}\n"
        f"# original_plan_file: {plan_file}\n"
        f"# work_dir: {work_dir.root}\n#\n"
        "# To requeue: fix the underlying problem, strip these comment lines,\n"
        f"# and move the file back into {plan_dir}/.\n#\n"
    )
    body = plan_file.read_text() if plan_file.exists() else ""
    quarantine_path.write_text(header + "\n" + body)

    # Remove from queue and clear the pending marker.
    plan_file.unlink(missing_ok=True)
    work_dir.pending.unlink(missing_ok=True)

    marker = Quarantined(
        plan_name=plan_name,
        plan_file=str(plan_file),
        status="quarantined",
        reason=reason,
        fix_attempts=attempts,
        quarantine_file=str(quarantine_path),
        quarantined_at=now(),
    )
    marker.write(work_dir.quarantined)
    log(f"Plan '{plan_name}' QUARANTINED after {attempts} failed fix attempt(s).")
    log(f"  moved to: {quarantine_path}")
    return marker
