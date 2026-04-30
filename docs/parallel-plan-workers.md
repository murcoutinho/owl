# Parallel plan workers proposal

Owl already executes each plan inside deterministic git worktrees under
`.work/worktrees/<plan>/<repo>`. That gives us the right isolation boundary for
running more than one plan at a time without touching the live sibling repos.

The safest way to add two-plan execution is to keep the current serial
`./src/owl.sh` path unchanged and add an explicit worker mode around it. The
worker mode should claim one eligible plan, run the existing execution and
review pipeline for that plan, then exit.

## Goals

- Run two independent plans concurrently.
- Reuse the existing per-plan worktree execution path.
- Preserve the current default single-process behavior.
- Avoid two workers claiming the same plan.
- Avoid running stacked plans before their base plan is ready.

## Non-goals

- Do not rewrite `execute_plan`, `run_review_loop`, or the PR creation flow for
  concurrency.
- Do not execute plans from the source sibling repos.
- Do not merge PRs automatically.
- Do not allow concurrent workers to edit the same plan workspace.

## Proposed interface

Add one low-level worker entrypoint:

```bash
./src/owl.sh --worker worker-1 --claim-one-plan
```

Then add a thin supervisor later:

```bash
./src/owl.sh --parallel 2
```

The supervisor can start two `--claim-one-plan` workers and wait for them. This
keeps the concurrency policy outside the existing plan execution core.

## Plan claiming

Workers need an atomic claim step before calling `execute_plan`.

Recommended shape:

1. Enumerate eligible plans using the same priority order as `check_plans`.
2. For each candidate, try to create a claim directory with `mkdir`, for
   example `.work/claims/<plan-slug>.lock`.
3. Write metadata into the claim directory:
   - worker id
   - pid
   - claimed timestamp
   - original plan path
4. Only the worker that creates the claim may execute that plan.
5. Remove the claim when the plan reaches a terminal state.
6. Leave the claim in place when the worker aborts mid-plan, so resume logic can
   inspect the preserved state before retrying.

Using `mkdir` keeps claiming atomic on the local filesystem and avoids a shared
shell lock around the whole run.

## Stacked plans

Plans with `base-branch` should not run while their base plan is still active.
Before claiming a plan, the scheduler should check whether its `base-branch`
points at another queued or claimed Owl plan.

If the base plan is queued or claimed, skip the dependent plan for this pass.
The existing stale-ref fallback still handles already-merged or deleted upstream
branches during reset and PR creation.

## Runtime state

Keep per-plan state as-is:

- `.work/worktrees/<plan-slug>/<repo>`
- `.work/<timestamp>_<plan-slug>/`
- `plan/<name>.md`
- `plan/done/<name>_<timestamp>.done.md`

Add worker-scoped state only where shared files would become hard to read:

- `.work/workers/<worker-id>/agent.log`
- `.work/claims/<plan-slug>.lock/`

The existing `.agent.lock` should continue protecting the default serial mode.
Parallel mode should use plan claims instead of holding the global lock for the
entire worker lifetime.

## Safety checks

Before enabling `--parallel`, add tests for:

- Two workers cannot claim the same plan.
- A claimed plan is skipped by the next worker.
- A stacked plan is skipped while its base plan is claimed.
- A worker uses `.work/worktrees/<plan>/<repo>` and leaves the source repo
  untouched.
- Default `./src/owl.sh` behavior remains serial and still honors `.agent.lock`.

This keeps the first implementation narrow: add the scheduler mechanics, keep
the proven per-plan execution core intact, and only then expose a convenience
`--parallel 2` wrapper.
