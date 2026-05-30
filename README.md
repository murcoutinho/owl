# Owl

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

**Want to spend your tokens while you sleep?** Owl is an autonomous dev agent that works through your plan queue overnight — writing code, running multi-model review rounds, and opening PRs across your sibling repos. Drop a markdown plan into `plan/`, start Owl, and wake up to reviewed pull requests ready to merge.

Owl orchestrates Claude, Codex, or both so implementation, fixes, and reviews each use your preferred model for the job. It stacks dependent plans, gates PRs behind your own deterministic test commands, resumes cleanly after rate limits, and keeps a full work log for every run.

## How it works

1. Drop a numbered markdown plan into `plan/`.
2. Run `owl`.
3. Owl picks the next eligible plan, creates a per-plan worktree for the target repo, resets the worktree to the right base, executes the plan, commits changes, runs review rounds, applies fixes, runs a final verification pass, pushes the branch, and opens a PR.
4. Owl writes a done file with the execution summary.

## Quick start

```bash
# 1. Clone owl next to the repo(s) you want it to work on
git clone https://github.com/YOUR_ORG/owl.git
cd owl

# 2. Install the Python package (Python 3.11+)
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .

# 3. Verify your environment (checks for claude/codex/gh and config)
owl --doctor

# 4. Copy a sample plan into the queue
cp examples/001-touch-readme.md plan/

# 5. Point owl at a sandbox repo and start it
export OWL_TARGET_REPOS=my-sandbox-repo
owl
```

See [`examples/`](examples/) for three runnable sample plans, including a
stacked-PR example that uses `base-branch` frontmatter.

## Repo layout

```text
owl/
├── owl/                        # Python package (entry point: `owl`)
├── plan/                       # your plan queue (gitignored)
│   └── done/
├── examples/                   # sample plans you can copy into plan/
├── skills/                     # Claude Code skills for plan authors and operators
├── tests/                      # pytest suite (unit, state_machine, integration)
└── README.md
```

## Plan format

Plan files live in `plan/` and use this naming format:

```text
NNN-short-kebab-case-title.md
```

Minimal example:

```md
---
repo: my-sandbox-repo
review-rounds: 1
priority: low
base-branch: owl/031-some-earlier-plan
---

# Add login page

## Context
...

## Working directory
...

## What to change
...

## Verification
...
```

Supported frontmatter:

- `repo` *(required)* — the single repo this plan touches. Must match one entry in `OWL_TARGET_REPOS`. A plan whose `repo:` is missing or unknown is rejected by `owl --validate` and skipped by the queue.
- `review-rounds` — `1` to `3`.
- `priority` — `low` or omitted.
- `base-branch` — branch name to stack on instead of `main`.

Each plan touches exactly one repo. Cross-repo work is expressed as two plans
chained via `base-branch:`. Keep plans self-contained — reviewers only see
the plan text plus diffs.

## Features

- Drains plans across multiple sibling repos (one repo per plan)
- Per-plan review rounds with configurable reviewer slots
- Deterministic test commands per repo via env config
- Verification pass that re-reviews after the final fix so PRs never ship with unaddressed reviewer findings
- Low-priority queue with `--skip-low-priority`
- Resume after crash or rate-limit abort
- Persistent Claude coder/fix session reuse
- Per-plan git worktrees under `.work/worktrees/`
- Done files and per-plan work logs under `.work/`

## Configuration

> **Tip.** Prefer Claude for the implementation/fix role when you care about context continuity and token efficiency across review rounds. Owl reuses a persistent per-plan Claude session for coder/fix work; Codex does not currently expose the same explicit `--session-id` control ([openai/codex#15271](https://github.com/openai/codex/issues/15271)).

Set env vars before running `owl`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OWL_TARGET_REPOS` | none | Space-separated sibling repo names. Required. |
| `OWL_PROJECT_DIR` | parent of `owl/` | Directory that contains the target repos. |
| `OWL_PLAN_DIR` | `$OWL_PROJECT_DIR/owl/plan` | Plan queue directory. |
| `OWL_WORK_DIR` | `$OWL_PROJECT_DIR/owl/.work` | Per-plan worktrees and run logs. |
| `OWL_IMPL_PROVIDER` | `claude` | Implementation provider: `claude` or `codex`. |
| `OWL_IMPL_MODEL` | `claude-opus-4-7` | Implementation model. |
| `OWL_FIX_PROVIDER` | `$OWL_IMPL_PROVIDER` | Fix-phase provider. |
| `OWL_FIX_MODEL` | `$OWL_IMPL_MODEL` | Fix-phase model. |
| `OWL_REVIEWER1_PROVIDER` | `codex` | Reviewer 1 provider: `claude`, `codex`, or `none`. |
| `OWL_REVIEWER1_MODEL` | `gpt-5.5` | Reviewer 1 model. Blank or `none` disables the slot. |
| `OWL_REVIEWER1_LABEL` | `Codex GPT 5.5` | Reviewer 1 label in logs/output. |
| `OWL_REVIEWER2_PROVIDER` | `codex` | Reviewer 2 provider: `claude`, `codex`, or `none`. |
| `OWL_REVIEWER2_MODEL` | `gpt-5.3-codex` | Reviewer 2 model. Blank or `none` disables the slot. |
| `OWL_REVIEWER2_LABEL` | `Codex GPT 5.3 Codex` | Reviewer 2 label in logs/output. |
| `OWL_REVIEW_MODE` | `parallel` | `parallel` or `sequential`. |
| `OWL_SKIP_LOW_PRIORITY` | `0` | When `1`, skip plans with `priority: low`. |
| `OWL_TEST_CMD_<repo>` | unset | Optional deterministic test command for one repo. |
| `OWL_TEST_SETUP_<repo>` | unset | Optional setup command run before `OWL_TEST_CMD_<repo>` (e.g. `npm ci` to install deps in a fresh worktree). A failing setup is reported as a test failure. |
| `OWL_LLM_TIMEOUT` | `2400` | Max seconds per LLM subprocess. |
| `OWL_POLL_INTERVAL_SECONDS` | `600` | Seconds between queue polls in default loop mode. |
| `REVIEW_ITERATIONS` | `2` | Default review rounds if plan omits `review-rounds`. |
| `RETRY_WAIT` | `600` | Seconds between rate-limit retries. |
| `MAX_RETRIES` | `50` | Max retry attempts. |

### Recommended local config

Put machine-specific config in the ignored `.env.local`:

```bash
OWL_TARGET_REPOS="project-api project-web"

OWL_IMPL_PROVIDER=claude
OWL_IMPL_MODEL=claude-opus-4-7
OWL_FIX_PROVIDER=claude
OWL_FIX_MODEL=claude-opus-4-7

OWL_REVIEWER1_PROVIDER=codex
OWL_REVIEWER1_MODEL=gpt-5.5
OWL_REVIEWER1_LABEL="Codex GPT 5.5"

OWL_REVIEWER2_PROVIDER=codex
OWL_REVIEWER2_MODEL=gpt-5.3-codex
OWL_REVIEWER2_LABEL="Codex GPT 5.3 Codex"

OWL_TEST_CMD_project_api="python -m pytest -q --tb=short"
OWL_TEST_CMD_project_web="npm test --silent"
# Each plan runs in a fresh git worktree, which contains tracked files only.
# Set OWL_TEST_SETUP_<repo> to install deps before tests run, or `jest` /
# `pytest` plugins / etc. won't be on PATH and the suite will exit 127.
OWL_TEST_SETUP_project_web="npm ci"
```

## Usage

```bash
owl --doctor                          # verify CLIs, auth, target repos
owl                                    # poll plan/ and run the queue (default)
owl --once                             # run one queue cycle and exit
owl --skip-low-priority                # daytime mode: defer priority: low
owl --validate plan/001-my-task.md     # parse a plan; no LLM, no git
owl --lint    plan/001-my-task.md      # pre-queue author lint (edit-target paths + sentinel)
owl --run-plan plan/001-my-task.md     # run one selected plan, then exit
```

Notes:

- `--doctor` does not call any LLM or touch git. Run it first on a new machine.
- `--validate` does not call any model or touch git.
- `--lint` is a pre-queue author check that flags absolute paths in edit-target sections and a missing "No plan-number references in code" sentinel. It does not call any model or touch git.
- `--skip-low-priority` only skips plans with `priority: low`.
- `--run-plan` skips the global queue lock and runs only the selected plan once. This lets you run two independent plans from separate terminals, but do not use it for plans that depend on each other via `base-branch`.
- Owl runs plans in deterministic per-plan worktrees, so dirty source repos no longer block execution.

## Requirements

- Python 3.11+
- Install/auth only the providers you configure
- `claude` CLI for Claude-backed roles
- `codex` CLI for Codex-backed roles
- `gh` CLI for PR creation
- Owl's checkout must sit beside the repos it manages (or set `OWL_PROJECT_DIR`)

## Skills

The repo ships two Claude Code skills:

- [`skills/owl-plan-author/SKILL.md`](skills/owl-plan-author/SKILL.md) — guides agents drafting new plans (numbering, frontmatter, anchors, queue hygiene).
- [`skills/owl-config/SKILL.md`](skills/owl-config/SKILL.md) — guides agents helping you configure Owl (target repos, models, test commands).

To install both for Claude Code, symlink them into your user-level skills directory:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/owl-plan-author" ~/.claude/skills/owl-plan-author
ln -s "$(pwd)/skills/owl-config"      ~/.claude/skills/owl-config
```

Restart Claude Code after installing or updating skills; skills are loaded at session start.

## Contributing

Bug reports, regression tests, and new provider integrations are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the test suite, lint expectations, and the kind of changes most likely to land.

## Security

Owl invokes its provider CLIs with their "unattended" flags — `claude --dangerously-skip-permissions` and `codex exec --full-auto --skip-git-repo-check` — and commits and pushes branches without prompting between steps. That's the whole value proposition: the agent has to run while you sleep, and every prompt it hits mid-run is a prompt you have to wake up to answer.

The isolation model is deliberately simple:

- **Scope is fenced at the repo layer.** Owl only touches directories listed in `OWL_TARGET_REPOS` that sit next to the `owl/` checkout. Everything outside that set is invisible to the agent.
- **Each plan touches exactly one repo.** The required `repo:` frontmatter field names the single target; Owl only creates a worktree for that repo, so the agent cannot edit a sibling repo by mistake.
- **Execution happens in per-plan worktrees.** Owl reuses deterministic worktrees under `.work/worktrees/<plan>/` so the live sibling repo can stay dirty while a plan runs or waits for review resume.
- **Humans still gate merges.** Owl opens pull requests — it does not merge them. Branch protection on your target repos is the last line of defense and should stay on.
- **`OWL_TEST_CMD_<repo>` and `OWL_TEST_SETUP_<repo>` run arbitrary shell.** Only set them from trusted `.env.local` config, never from plan content or LLM output.
- **The plan queue is trusted input.** Anyone who can write to `plan/` can direct the agent. Treat `plan/` the same way you treat your shell history or `~/.ssh/config`.

Use Owl on repositories you own or are authorized to change, run it against a branch-protected target, and review every PR before merging. If you are not comfortable with an autonomous process pushing branches on your behalf, this is not the tool for you.

## License

MIT
