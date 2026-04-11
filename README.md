# Owl

![shellcheck](https://github.com/murcoutinho/owl/actions/workflows/shellcheck.yml/badge.svg)

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

**Want to spend your tokens while you sleep?** Owl is an autonomous dev agent that works through your plan queue overnight — writing code, running multi-model review rounds, and opening PRs across one or more sibling repos. Drop a markdown plan into `plan/`, start Owl, and wake up to reviewed pull requests ready to merge.

Owl orchestrates Claude, Codex, or both so implementation, fixes, and reviews each use your preferred model for the job. It stacks dependent plans, gates PRs behind your own deterministic test commands, resumes cleanly after rate limits, and keeps a full work log for every run.

## How it works

1. Drop a numbered markdown plan into `plan/`.
2. Run `./src/owl.sh`.
3. Owl picks the next eligible plan, resets target repos to the right base, executes the plan, commits changes, runs review rounds, applies fixes, pushes branches, and opens PRs.
4. Owl writes a done file with the execution summary.

## Quick start

```bash
# 1. Clone owl next to the repo(s) you want it to work on
git clone https://github.com/murcoutinho/owl.git
cd owl

# 2. Verify your environment (checks for claude/codex/gh and config)
./src/owl.sh --doctor

# 3. Copy a sample plan into the queue
cp examples/001-touch-readme.md plan/

# 4. Point owl at a sandbox repo and start it
export OWL_TARGET_REPOS=my-sandbox-repo
./src/owl.sh
```

See [`examples/`](examples/) for three runnable sample plans, including a
stacked-PR example that uses `base-branch` frontmatter.

## Repo layout

```text
owl/
├── src/owl.sh
├── plan/                       # your plan queue (gitignored)
│   └── done/
├── examples/                   # sample plans you can copy into plan/
├── skills/owl-plan-author/     # skill for agents drafting new plans
├── tests/                      # bash test suite for owl.sh
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
review-rounds: 1
priority: low
base-branch: owl/031-some-earlier-plan
---

# Add login page

## Context
...

## Working directory
/abs/path/to/repo

## What to change
...

## Verification
...
```

Supported frontmatter:

- `review-rounds`: `1` to `3`
- `priority`: `low` or omitted
- `base-branch`: branch name to stack on instead of `main`

Keep plans self-contained. Reviewers only see the plan text plus diffs.

## Features

- Multi-repo execution across configured sibling repos
- Per-plan review rounds with configurable reviewer slots
- Deterministic test commands per repo via env config
- Low-priority queue with `--skip-low-priority`
- Resume after crash or rate-limit abort
- Persistent Claude coder/fix session reuse
- Done files and per-plan work logs under `.work/`

## Configuration

> **Tip.** Prefer Claude for the implementation/fix role when you care about context continuity and token efficiency across review rounds. Owl reuses a persistent per-plan Claude session for coder/fix work; Codex does not currently expose the same explicit `--session-id` control ([openai/codex#15271](https://github.com/openai/codex/issues/15271)).

Set env vars before running `src/owl.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OWL_TARGET_REPOS` | none | Space-separated sibling repo names. Required. |
| `OWL_IMPL_PROVIDER` | `claude` | Implementation provider: `claude` or `codex`. |
| `OWL_IMPL_MODEL` | `claude-sonnet-4-6` | Implementation model. |
| `OWL_FIX_PROVIDER` | `OWL_IMPL_PROVIDER` | Fix-phase provider. |
| `OWL_FIX_MODEL` | `OWL_IMPL_MODEL` | Fix-phase model. |
| `OWL_REVIEWER1_PROVIDER` | `codex` | Reviewer 1 provider: `claude`, `codex`, or `none`. |
| `OWL_REVIEWER1_MODEL` | `gpt-5.4` | Reviewer 1 model. Blank or `none` disables the slot. |
| `OWL_REVIEWER1_LABEL` | `Codex` | Reviewer 1 label in logs/output. |
| `OWL_REVIEWER2_PROVIDER` | `claude` | Reviewer 2 provider: `claude`, `codex`, or `none`. |
| `OWL_REVIEWER2_MODEL` | `claude-sonnet-4-6` | Reviewer 2 model. Blank or `none` disables the slot. |
| `OWL_REVIEWER2_LABEL` | `Claude Code` | Reviewer 2 label in logs/output. |
| `OWL_REVIEW_MODE` | `parallel` | `parallel` or `sequential`. |
| `OWL_SKIP_LOW_PRIORITY` | `0` | When `1`, skip plans with `priority: low`. |
| `OWL_TEST_CMD_<repo>` | unset | Optional deterministic test command for one repo. |
| `REVIEW_ITERATIONS` | `2` | Default review rounds if plan omits `review-rounds`. |
| `RETRY_WAIT` | `600` | Seconds between rate-limit retries. |
| `MAX_RETRIES` | `50` | Max retry attempts. |

### Recommended local config

Put machine-specific config in the ignored `.env.local`:

```bash
OWL_TARGET_REPOS="owl saudade saudade-mobile"

OWL_IMPL_PROVIDER=claude
OWL_IMPL_MODEL=claude-opus-4-6
OWL_FIX_PROVIDER=claude
OWL_FIX_MODEL=claude-opus-4-6

OWL_REVIEWER1_PROVIDER=codex
OWL_REVIEWER1_MODEL=gpt-5.4
OWL_REVIEWER1_LABEL="ChatGPT GPT-5.4"

OWL_REVIEWER2_PROVIDER=none
OWL_REVIEWER2_MODEL=none

OWL_TEST_CMD_saudade="python -m pytest -q --tb=short"
OWL_TEST_CMD_saudade_mobile="npm test --silent"
```

## Usage

```bash
./src/owl.sh --doctor                        # verify CLIs, auth, target repos
./src/owl.sh                                  # poll plan/ and run the queue
./src/owl.sh --skip-low-priority              # daytime mode: defer priority: low
./src/owl.sh --validate plan/001-my-task.md   # dry-parse a plan, no LLM calls
tail -f agent.log
```

Notes:

- `--doctor` does not call any LLM or touch git. Run it first on a new machine.
- `--skip-low-priority` only skips plans with `priority: low`.
- `--validate` does not call any model or touch git.
- If a repo already has local changes, Owl aborts before calling the coder.

## Requirements

- Install/auth only the providers you configure
- `claude` CLI for Claude-backed roles
- `codex` CLI for Codex-backed roles
- `gh` CLI for PR creation
- Owl must sit beside the repos it manages

## Skill

The repo includes a plan-authoring skill at [`skills/owl-plan-author/SKILL.md`](skills/owl-plan-author/SKILL.md). Use it when you want an agent to draft a new Owl plan with the right numbering, frontmatter, and queue hygiene.

## Contributing

Bug reports, test cases, and new provider integrations are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the test suite, shellcheck expectations, and the kind of changes most likely to land.

## Security

Owl invokes its provider CLIs with their "unattended" flags — `claude --dangerously-skip-permissions` and `codex exec --full-auto --skip-git-repo-check` — and commits and pushes branches without prompting between steps. That's the whole value proposition: the agent has to run while you sleep, and every prompt it hits mid-run is a prompt you have to wake up to answer.

The isolation model is deliberately simple:

- **Scope is fenced at the repo layer.** Owl only touches directories listed in `OWL_TARGET_REPOS` that sit next to the `owl/` checkout. Everything outside that set is invisible to the agent.
- **Humans still gate merges.** Owl opens pull requests — it does not merge them. Branch protection on your target repos is the last line of defense and should stay on.
- **`OWL_TEST_CMD_<repo>` runs arbitrary shell.** Only set it from trusted `.env.local` config, never from plan content or LLM output.
- **The plan queue is trusted input.** Anyone who can write to `plan/` can direct the agent. Treat `plan/` the same way you treat your shell history or `~/.ssh/config`.

Use Owl on repositories you own or are authorized to change, run it against a branch-protected target, and review every PR before merging. If you are not comfortable with an autonomous process pushing branches on your behalf, this is not the tool for you.

## License

MIT
