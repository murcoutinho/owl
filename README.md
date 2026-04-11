# Owl

![shellcheck](https://github.com/YOUR_ORG/owl/actions/workflows/shellcheck.yml/badge.svg)

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

Owl is an autonomous dev agent that executes queued plans, commits code, runs review rounds, and opens PRs across one or more sibling repos.

> **Security notice.** Owl runs `claude --dangerously-skip-permissions` and `codex exec --full-auto --skip-git-repo-check` against your code, then commits and pushes branches. Use it only on repos you trust.

> **Recommendation.** Prefer Claude for the implementation/fix role when you care about context continuity and token efficiency across review rounds. Owl reuses a persistent per-plan Claude session for coder/fix work. Codex does not currently expose the same explicit `--session-id` control Owl uses for that feature: https://github.com/openai/codex/issues/15271?utm_source=chatgpt.com&issue=openai%7Ccodex%7C7801

## How it works

1. Drop a numbered markdown plan into `plan/`.
2. Run `./src/owl.sh`.
3. Owl picks the next eligible plan, resets target repos to the right base, executes the plan, commits changes, runs review rounds, applies fixes, pushes branches, and opens PRs.
4. Owl writes a done file with the execution summary.

## Repo layout

```text
owl/
├── src/owl.sh
├── plan/
│   └── done/
├── skills/owl-plan-author/SKILL.md
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
OWL_TARGET_REPOS="owl project-api project-web"

OWL_IMPL_PROVIDER=claude
OWL_IMPL_MODEL=claude-opus-4-6
OWL_FIX_PROVIDER=claude
OWL_FIX_MODEL=claude-opus-4-6

OWL_REVIEWER1_PROVIDER=codex
OWL_REVIEWER1_MODEL=gpt-5.4
OWL_REVIEWER1_LABEL="ChatGPT GPT-5.4"

OWL_REVIEWER2_PROVIDER=none
OWL_REVIEWER2_MODEL=none

OWL_TEST_CMD_project-api="python -m pytest -q --tb=short"
OWL_TEST_CMD_project-api_mobile="npm test --silent"
```

## Usage

```bash
./src/owl.sh
./src/owl.sh --skip-low-priority
./src/owl.sh --validate plan/001-my-task.md
tail -f agent.log
```

Notes:

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

The repo includes a plan-authoring skill at [skills/owl-plan-author/SKILL.md](/Users/user/path/to/projects/owl/skills/owl-plan-author/SKILL.md). Use it when you want an agent to draft a new Owl plan with the right numbering, frontmatter, and queue hygiene.

## License

MIT
