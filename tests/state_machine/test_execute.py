"""State-machine tests for owl.plan_runner.execute.

These drive execute_plan with a FakeGit universe and FakeLLM and assert the
resulting transition and on-disk state. No real git, no real LLM.
"""

from __future__ import annotations

from pathlib import Path

from owl.plan_runner.execute import ExecuteOutcome, execute_plan
from owl.subprocess_.llm import LLMResult
from tests.conftest import FakeGitUniverse


def _coder_writes(universe: FakeGitUniverse, repo_name: str):
    """Return a diff_writer that marks the worktree repo dirty (coder edited files)."""

    def writer(cwd: Path) -> None:
        universe.state(cwd / repo_name).dirty = True

    return writer


# ─── executing → pending_after_exec ─────────────────────────────────────────


def test_execute_happy_path_marks_pending(make_plan, make_deps, git_universe, fake_llm, work_root):
    plan = make_plan(repo="saudade")
    deps = make_deps()
    fake_llm.diff_writer = _coder_writes(git_universe, "saudade")

    result = execute_plan(plan, deps, work_root=work_root)

    assert result.outcome == ExecuteOutcome.PENDING_AFTER_EXEC
    ctx = result.context
    assert ctx is not None
    # pending + branch markers written
    assert ctx.work_dir.pending.exists()
    assert ctx.work_dir.branch.read_text().strip() == "owl/001-feature"
    # review_input_1 has the committed repo
    assert "saudade" in ctx.work_dir.review_input(1).read_text()
    # branch created in the worktree
    wt = git_universe.state(ctx.worktree_repo_root)
    assert "owl/001-feature" in wt.branches


def test_execute_writes_execution_base_and_review_iterations(
    make_plan, make_deps, git_universe, fake_llm, work_root
):
    plan = make_plan(repo="saudade", extra_fm="review-rounds: 3\n")
    deps = make_deps()
    fake_llm.diff_writer = _coder_writes(git_universe, "saudade")

    result = execute_plan(plan, deps, work_root=work_root)
    wd = result.context.work_dir
    assert wd.review_iterations.read_text().strip() == "3"
    assert "saudade" in wd.execution_base_tsv.read_text()


# ─── executing → completed (no-op) ──────────────────────────────────────────


def test_execute_no_changes_is_no_op(make_plan, make_deps, fake_llm, work_root):
    plan = make_plan(repo="saudade")
    deps = make_deps()
    # No diff_writer → coder "succeeds" but leaves the worktree clean.
    fake_llm.script = [LLMResult(rc=0, output="done, nothing needed", elapsed_sec=1, timed_out=False, rate_limited=False)]

    result = execute_plan(plan, deps, work_root=work_root)
    assert result.outcome == ExecuteOutcome.COMPLETED_NO_OP
    assert not result.context.work_dir.pending.exists()


# ─── executing → retry ──────────────────────────────────────────────────────


def test_execute_llm_failure_retries_without_pending(make_plan, make_deps, fake_llm, work_root):
    plan = make_plan(repo="saudade")
    deps = make_deps()
    fake_llm.script = [LLMResult(rc=1, output="", elapsed_sec=1, timed_out=False, rate_limited=False)]

    result = execute_plan(plan, deps, work_root=work_root)
    assert result.outcome == ExecuteOutcome.RETRY
    assert not result.context.work_dir.pending.exists()


def test_execute_llm_empty_output_retries(make_plan, make_deps, fake_llm, work_root):
    plan = make_plan(repo="saudade")
    deps = make_deps()
    fake_llm.script = [LLMResult(rc=0, output="   ", elapsed_sec=1, timed_out=False, rate_limited=False)]

    result = execute_plan(plan, deps, work_root=work_root)
    assert result.outcome == ExecuteOutcome.RETRY


def test_execute_commit_failure_retries(make_plan, make_deps, git_universe, fake_llm, work_root):
    plan = make_plan(repo="saudade")
    deps = make_deps()

    def writer(cwd: Path) -> None:
        state = git_universe.state(cwd / "saudade")
        state.dirty = True
        state.commit_should_fail = True  # simulate a commit that won't land

    fake_llm.diff_writer = writer
    result = execute_plan(plan, deps, work_root=work_root)
    assert result.outcome == ExecuteOutcome.RETRY
    assert not result.context.work_dir.pending.exists()


# ─── plan-286 regression: workspace contains only the declared repo ─────────


def test_repo_field_isolates_workspace(make_plan, make_deps, git_universe, fake_llm, work_root):
    """A plan declaring `repo: saudade` must never create a worktree for any
    other repo. Even if the coder dirties a raven path, raven is not in the
    workspace, so normalize cannot touch it — the plan-286 bug class is gone."""
    plan = make_plan(repo="saudade")
    deps = make_deps(target_repos=("saudade", "saudade-mobile", "raven"))
    fake_llm.diff_writer = _coder_writes(git_universe, "saudade")

    result = execute_plan(plan, deps, work_root=work_root)
    ctx = result.context

    # Only saudade is in the workspace.
    assert list(ctx.workspace.repo_roots.keys()) == ["saudade"]

    # The other source repos never had the plan branch created on them.
    raven_src = git_universe.state(deps.cfg.project_dir / "raven")
    mobile_src = git_universe.state(deps.cfg.project_dir / "saudade-mobile")
    assert "owl/001-feature" not in raven_src.branches
    assert "owl/001-feature" not in mobile_src.branches
