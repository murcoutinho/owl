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

A plan can override the default number of review-fix cycles via YAML frontmatter.
Values are clamped to `[1, 3]`; anything missing or invalid falls back to the
`REVIEW_ITERATIONS` default.

```markdown
---
review-rounds: 3
---

# Add login page
...
```

## Features

- **Rate limit resilience** — retries every 10 minutes on 429/overloaded, up to ~8 hours
- **Resume on crash** — pending reviews survive restarts via state files
- **Multi-repo** — commits and reviews across all git repos in the parent directory
- **Targeted diffs** — each review round sees exactly its own commit, not HEAD~1
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
