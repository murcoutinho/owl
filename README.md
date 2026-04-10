# Owl

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

An autonomous dev agent that executes plans using Claude Code and/or Codex, with built-in review cycles.

## How It Works

1. Drop a `.md` plan file into `plan/` with a numeric prefix (e.g., `001-my-task.md`)
2. Start the agent: `./src/agent.sh`
3. Every 10 minutes, the agent picks up the next plan by order and:
   - **Executes** the plan via the configured provider
   - **Commits** all changes across any git repos in the parent directory
   - **Reviews** the commit with the configured reviewer slots
   - **Fixes** issues from the review via the configured provider
   - **Repeats** the review-fix cycle (2 iterations by default)
   - **Writes** a done file with execution summary, commits, and review content

## Structure

```
owl/
├── src/
│   └── agent.sh        # The agent (main loop + review engine)
├── plan/               # Drop .md plan files here (numbered: 001-, 002-, ...)
│   └── done/           # Completed plans with execution summaries
└── README.md
```

## Plan Format

Plans are markdown files describing what to build. Include:
- **Working directory** — which repo to work in
- **What to do** — clear implementation steps
- **Files to create/modify** — so the agent knows scope
- **Verification** — how to test the result

Example: `003-add-login-page.md`

### Optional frontmatter

Plans can declare configuration via YAML frontmatter. All fields are optional.

```markdown
---
review-rounds: 3
base-branch: owl/019-earlier-dependency
---

# Add login page
...
```

**`review-rounds`** — override the default number of review-fix cycles.
Values are clamped to `[1, 3]`; anything missing or invalid falls back to the
`REVIEW_ITERATIONS` default.

**`base-branch`** — declare a dependency on another plan's branch. Instead of
starting from `main`, the agent checks out the named branch before executing.
Use this when a plan depends on work that is still sitting in a queued or
in-flight plan (and whose PR may not be merged yet). The value must be a
literal branch name — owl does not cross-reference other plan files.

At execution time:

- For each repo, owl tries `git fetch origin <base-branch>`. If origin has
  the branch, the repo is checked out to it and the plan is built on top.
- If origin does NOT have the branch (the dependency was already merged and
  deleted), that repo silently falls back to `main`. This is the expected
  happy path once the dependency ships.
- PRs opened for the new plan target the same base: `gh pr create --base`
  uses the resolved per-repo base, so stacked PRs point at the right parent.

Example chain: plan `019` touches `saudade`. Plan `020` touches both `saudade`
and `saudade-mobile`, and the `saudade` half depends on plan `019`. Add
`base-branch: owl/019-...` to plan `020`. When owl runs `020`, the `saudade`
repo checks out `019`'s branch and the new commits stack on top; the
`saudade-mobile` repo has no `019` branch on origin and silently uses `main`.

## Features

- **Rate limit resilience** — retries every 10 minutes on 429/overloaded, up to ~8 hours
- **Resume on crash** — pending reviews survive restarts via state files
- **Multi-repo** — commits and reviews across all git repos in the parent directory
- **Targeted diffs** — each review round sees exactly its own commit, not HEAD~1
- **Plan-aware review** — reviewers and the fix agent both receive the plan text alongside the diff, so deliberate changes that match the plan are not flagged as regressions
- **Stacked plans** — a plan can declare `base-branch:` in its frontmatter to start from another plan's branch instead of `main`, allowing dependent plans to queue before their parents are merged
- **Lock file** — prevents concurrent agent instances
- **Done files** — full audit trail with repos changed, commit hashes, and review content

## Configuration

Set env vars before starting `src/agent.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OWL_IMPL_PROVIDER` | `claude` | Provider for plan execution: `claude` or `codex` |
| `OWL_IMPL_MODEL` | `claude-sonnet-4-6` | Model for plan execution |
| `OWL_FIX_PROVIDER` | `OWL_IMPL_PROVIDER` | Provider for fix phase: `claude` or `codex` |
| `OWL_FIX_MODEL` | `OWL_IMPL_MODEL` | Model for fix phase |
| `OWL_REVIEWER1_PROVIDER` | `codex` | Reviewer slot 1 provider: `claude`, `codex`, or `none` |
| `OWL_REVIEWER1_MODEL` | `gpt-5.4` | Reviewer slot 1 model |
| `OWL_REVIEWER1_LABEL` | `Codex` | Reviewer slot 1 label in logs/output |
| `OWL_REVIEWER2_PROVIDER` | `claude` | Reviewer slot 2 provider: `claude`, `codex`, or `none` |
| `OWL_REVIEWER2_MODEL` | `claude-sonnet-4-6` | Reviewer slot 2 model |
| `OWL_REVIEWER2_LABEL` | `Claude Code` | Reviewer slot 2 label in logs/output |
| `OWL_REVIEW_MODE` | `parallel` | Reviewer scheduling: `parallel` or `sequential` |
| `REVIEW_ITERATIONS` | 2 | Default review-fix cycles per plan (plans may override up to 3 via frontmatter) |
| `RETRY_WAIT` | 600 | Seconds between rate-limit retries |
| `MAX_RETRIES` | 50 | Max retry attempts (~8 hours) |

Example: Codex-only execution/fixes, Codex+Claude review:

```bash
export OWL_IMPL_PROVIDER=codex
export OWL_IMPL_MODEL=gpt-5.4
export OWL_FIX_PROVIDER=codex
export OWL_FIX_MODEL=gpt-5.4
export OWL_REVIEWER1_PROVIDER=codex
export OWL_REVIEWER1_MODEL=gpt-5.4
export OWL_REVIEWER2_PROVIDER=claude
export OWL_REVIEWER2_MODEL=claude-sonnet-4-6
```

## Requirements

- Install/auth only the providers you actually configure
- `claude` CLI is required for any role using `claude`
- `codex` CLI is required for any role using `codex`
- The agent directory must be inside a parent directory containing git repos

## Usage

```bash
# Start the agent
./src/agent.sh

# Watch progress
tail -f agent.log

# Stop
Ctrl+C or kill the process
```

## Notes

- Claude-backed roles use `claude --dangerously-skip-permissions` — review plans before enabling them
- Codex-backed roles use `codex exec --full-auto --skip-git-repo-check`
- All logs go to `agent.log`
- Work directories (`.work/`) contain per-plan execution logs, review files, and commit manifests
