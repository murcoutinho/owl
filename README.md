# Owl

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

An autonomous dev agent that executes plans using Codex, with built-in review cycles.

## How It Works

1. Drop a `.md` plan file into `plan/` with a numeric prefix (e.g., `001-my-task.md`)
2. Start the agent: `./src/agent.sh`
3. Every 10 minutes, the agent picks up the next plan by order and:
   - **Executes** the plan via Codex
   - **Commits** all changes across any git repos in the parent directory
   - **Reviews** the commit via Codex
   - **Fixes** issues from the review via Codex
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

## Features

- **Rate limit resilience** — retries every 10 minutes on 429/overloaded, up to ~8 hours
- **Resume on crash** — pending reviews survive restarts via state files
- **Multi-repo** — commits and reviews across all git repos in the parent directory
- **Targeted diffs** — each review round sees exactly its own commit, not HEAD~1
- **Lock file** — prevents concurrent agent instances
- **Done files** — full audit trail with repos changed, commit hashes, and review content

## Configuration

Edit the top of `src/agent.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `REVIEW_ITERATIONS` | 2 | Number of review-fix cycles per plan |
| `RETRY_WAIT` | 600 | Seconds between rate-limit retries |
| `MAX_RETRIES` | 50 | Max retry attempts (~8 hours) |

## Requirements

- `codex` CLI installed and authenticated (with `--skip-git-repo-check`)
- `codex` available on `$PATH`
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

- Uses `codex --dangerously-bypass-approvals-and-sandbox` — review plans before dropping them in
- All logs go to `agent.log`
- Work directories (`.work/`) contain per-plan execution logs, review files, and commit manifests
