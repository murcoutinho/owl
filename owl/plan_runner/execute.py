"""Execute a plan: steps 2-5 (workspace, reset, LLM, branch+commit, mark pending).

Ports the body of ``execute_plan`` up to the review loop (owl.sh:1356-1468).
The review loop itself is driven by the caller (the runner), so this function
is testable in isolation: it returns an ``ExecuteResult`` describing the
state transition rather than recursing into review.

Transitions produced here (see the plan's state diagram):

* PENDING_AFTER_EXEC — branch created, changes committed, ``pending`` written.
* COMPLETED_NO_OP    — coder produced no changes; nothing to review.
* RETRY              — LLM failed/empty, or a commit failed leaving dirt;
                       no ``pending`` written, the cycle should retry later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..deps import Deps
from ..plans.model import Plan
from ..state import session
from ..state.workspace import PlanWorkDir, ensure_workspace
from ..subprocess_.llm import run_slot
from ..subprocess_.prompts import write_wrapped_prompt
from .context import PlanContext, repos_for_plan
from .normalize import normalize_all, write_execution_base
from .reset import reset_repos_to_base, switch_all_to_main

_PLAN_INSTRUCTIONS = """

IMPORTANT INSTRUCTIONS:
- Do NOT commit, push, or create branches. Just write the code changes.
- The agent handles all git operations.
"""


class ExecuteOutcome(StrEnum):
    PENDING_AFTER_EXEC = "pending_after_exec"
    COMPLETED_NO_OP = "completed_no_op"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    outcome: ExecuteOutcome
    context: PlanContext | None
    note: str = ""


def execute_plan(plan: Plan, deps: Deps, *, work_root) -> ExecuteResult:
    cfg = deps.cfg
    log = deps.log

    work_dir = PlanWorkDir.create(work_root, plan.slug, timestamp=_timestamp(deps))
    work_dir.review_iterations.write_text(f"{plan.fm.review_rounds}\n")
    if plan.fm.base_branch:
        work_dir.base_branch.write_text(plan.fm.base_branch + "\n")

    branch_name = f"owl/{plan.slug}"
    source_repos = repos_for_plan(plan, cfg)

    coder_session_id = None
    if cfg.impl.provider == "claude":
        coder_session_id = session.get_or_create(work_dir.coder_session_id)
        log(f"Using Claude coder session: {coder_session_id}")

    # ── Step 2: workspace (single repo only) + reset to base ────────────────
    workspace = ensure_workspace(
        work_root=work_root,
        plan_slug=plan.slug,
        repos=source_repos,
        git=deps.git,
        log=log,
    )
    if workspace is None or not workspace.repo_roots:
        log("Plan execution aborted: failed to prepare worktree.")
        return ExecuteResult(ExecuteOutcome.RETRY, None, "workspace setup failed")

    work_dir.execution_project_dir.write_text(str(workspace.root) + "\n")
    reset_repos_to_base(
        repos=workspace.repo_roots,
        git=deps.git,
        base_branch=plan.fm.base_branch,
        discard_owned_changes=True,
        log=log,
    )
    write_execution_base(repos=workspace.repo_roots, git=deps.git, work_dir=work_dir)

    ctx = PlanContext(
        plan=plan,
        work_dir=work_dir,
        workspace=workspace,
        branch_name=branch_name,
        repo_name=plan.fm.repo,  # type: ignore[arg-type]
    )

    # ── Step 3: execute ──────────────────────────────────────────────────────
    log(f"[Step 3] Executing plan via {cfg.impl.provider} ({cfg.impl.model})...")
    write_wrapped_prompt(
        work_dir.plan_prompt_wrapped,
        ack_path=work_dir.coder_ack,
        base_prompt=plan.body + _PLAN_INSTRUCTIONS,
        worktree_root=workspace.root,
        project_dir=cfg.project_dir,
        sub_repos=list(workspace.repo_roots.keys()),
    )

    with deps.ack_watcher("coder", work_dir.coder_ack):
        result = run_slot(
            deps.llm,
            cfg.impl,
            work_dir.plan_prompt_wrapped,
            session_id=coder_session_id,
            session_mode="create",
            cwd=workspace.root,
        )
    work_dir.execution_log.write_text(result.output)

    if not result.ok or not result.output.strip():
        log(
            f"Plan execution failed (exit={result.rc}, output_len={len(result.output)}). "
            "Will retry next cycle."
        )
        _discard_partial(ctx, deps)
        return ExecuteResult(ExecuteOutcome.RETRY, ctx, "llm failed or produced no output")

    log("Plan execution completed.")

    # ── Step 4: branch + commit ──────────────────────────────────────────────
    log("[Step 4] Creating branch and committing changes...")
    norm = normalize_all(
        repos=workspace.repo_roots,
        git=deps.git,
        branch_name=branch_name,
        commit_message=f"[owl] {plan.slug} — execution",
        work_dir=work_dir,
        branch_mode="create",
        log=log,
    )
    _seed_manifest(ctx, norm.committed)

    if norm.had_error:
        log("Repo normalization failed during execution commit phase. Will retry next cycle.")
        switch_all_to_main(repos=workspace.repo_roots, git=deps.git, log=log)
        return ExecuteResult(ExecuteOutcome.RETRY, ctx, "normalize error")

    if not norm.made_a_commit:
        if _any_repo_dirty(ctx, deps):
            log("Plan produced changes but commits failed. Will retry next cycle.")
            switch_all_to_main(repos=workspace.repo_roots, git=deps.git, log=log)
            return ExecuteResult(ExecuteOutcome.RETRY, ctx, "commit failed, dirty")
        log("Plan produced no changes in any repo. Marking done with no-op summary.")
        switch_all_to_main(repos=workspace.repo_roots, git=deps.git, log=log)
        return ExecuteResult(
            ExecuteOutcome.COMPLETED_NO_OP,
            ctx,
            "No repo changes were needed.",
        )

    # ── Step 5: mark pending ─────────────────────────────────────────────────
    work_dir.pending.write_text(str(plan.path) + "\n")
    work_dir.branch.write_text(branch_name + "\n")
    return ExecuteResult(ExecuteOutcome.PENDING_AFTER_EXEC, ctx, "")


# ─── helpers ─────────────────────────────────────────────────────────────────


def _timestamp(deps: Deps) -> str:
    # work_id uses a filename-safe stamp; reuse deps.now() but strip spaces/colons.
    return deps.now().replace("-", "").replace(":", "").replace(" ", "_")


def _seed_manifest(ctx: PlanContext, committed) -> None:
    from ..state import manifest

    path = ctx.work_dir.review_input(1)
    path.write_text("")
    for entry in committed:
        manifest.append_manifest_row(path, entry)


def _discard_partial(ctx: PlanContext, deps: Deps) -> None:
    for repo_name, repo_root in ctx.repos.items():
        g = deps.git(repo_root)
        if g.has_local_changes():
            deps.log(f"  Discarding partial changes in {repo_name} (failed plan)")
            g.reset_hard()
            g.clean()


def _any_repo_dirty(ctx: PlanContext, deps: Deps) -> bool:
    return any(deps.git(root).has_local_changes() for root in ctx.repos.values())
