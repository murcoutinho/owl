# Owl

![shellcheck](https://github.com/murcoutinho/owl/actions/workflows/shellcheck.yml/badge.svg)

<p align="center">
  <img src="owl.png" width="400" alt="Owl — autonomous dev agent" />
</p>

An autonomous dev agent that executes plans using Claude Code and/or Codex, with built-in review cycles.

> **⚠️ Security notice.** Owl runs `claude --dangerously-skip-permissions`
> and `codex --full-auto --skip-git-repo-check` against your code, then
> commits and pushes branches to GitHub. Use it only in repositories you
> trust and have reviewed, and make sure your providers' scopes match what
> you are comfortable letting an agent do. Review every PR before merging.

## How It Works

1. Drop a `.md` plan file into `plan/` with a numeric prefix (e.g., `001-my-task.md`)
2. Start the agent: `./src/owl.sh`
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
│   └── owl.sh          # The agent (main loop + review engine)
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
priority: low
base-branch: owl/019-earlier-dependency
---

# Add login page
...
```

**`review-rounds`** — override the default number of review-fix cycles.
Values are clamped to `[1, 3]`; anything missing or invalid falls back to the
`REVIEW_ITERATIONS` default.

**`priority`** — set to `low` to mark the plan as low-priority. Each cycle
runs in two passes: normal-priority plans first (in filename order), then
low-priority plans (in filename order) after every normal plan has been
executed. This guarantees a nice-to-have with a smaller numeric prefix
never blocks a higher-value plan queued later — e.g. with `023-lp.md`,
`024-normal.md`, `025-lp.md`, the execution order is `024 → 023 → 025`.

When the agent is started with `--skip-low-priority` (or
`OWL_SKIP_LOW_PRIORITY=1`), the second pass is a no-op — every
`priority: low` plan is logged and bypassed. This lets you run the agent
during the day with the flag set (saving tokens on nice-to-haves) and
drop the flag at night so the low-priority queue drains overnight. Any
value other than `low` is treated as normal priority, and omitting the
field behaves the same way.

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
- **Resume on crash or mid-review abort** — pending plans survive restarts and unrecoverable LLM failures; if a reviewer or the fix agent gives up after `MAX_RETRIES` rate-limited attempts, Owl persists a `pending_status` file (plan, branch, failed iteration, reason) and resumes the plan before picking up anything new on the next cycle. Prevents silently marking a plan "done" with zero successful review rounds.
- **Multi-repo** — commits and reviews across all git repos in the parent directory
- **Targeted diffs** — each review round sees exactly its own commit, not HEAD~1
- **Plan-aware review** — reviewers and the fix agent both receive the plan text alongside the diff, so deliberate changes that match the plan are not flagged as regressions
- **Deterministic test gate** — opt-in per repo via `OWL_TEST_CMD_<repo>`; Owl runs the command at the top of every review round and feeds failing output to the fix agent alongside LLM reviewer feedback, and refuses to LGTM-exit while any suite is red
- **Stacked plans** — a plan can declare `base-branch:` in its frontmatter to start from another plan's branch instead of `main`, allowing dependent plans to queue before their parents are merged
- **Low-priority queue** — plans marked `priority: low` always drain *after* every normal-priority plan in the same cycle (two-pass), so a cheap nice-to-have with a smaller numeric prefix never blocks higher-value work. `--skip-low-priority` bypasses them entirely during the day and overnight runs drain them.
- **Lock file** — prevents concurrent agent instances
- **Done files** — full audit trail with repos changed, commit hashes, and review content

## Configuration

Set env vars before starting `src/owl.sh`:

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
| `OWL_SKIP_LOW_PRIORITY` | `0` | When `1`, skip plans with `priority: low` in frontmatter. Equivalent to the `--skip-low-priority` CLI flag. |
| `OWL_TEST_CMD_<repo>` | *(unset)* | Optional deterministic (non-LLM) test command for one repo. See "Deterministic test gate" below. |
| `RETRY_WAIT` | 600 | Seconds between rate-limit retries |
| `MAX_RETRIES` | 50 | Max retry attempts (~8 hours) |

### Deterministic test gate

Owl can run your project's real test suite on every review round and feed any
failures to the fix agent alongside the LLM reviewer feedback. This catches
regressions that slip past the reviewers (whose input is limited to the diff
and the plan text) and anchors iteration on objective signal instead of just
model opinion.

Opt in per repo by setting `OWL_TEST_CMD_<repo_name>` in your private
`.env.local` (the same file that holds `OWL_TARGET_REPOS`). Hyphens in the
repo name are replaced with underscores in the variable name:

```bash
# .env.local
OWL_TARGET_REPOS="saudade saudade-mobile"
OWL_TEST_CMD_saudade="python -m pytest -q --tb=short"
OWL_TEST_CMD_saudade_mobile="npm test --silent"
```

Behavior:

- At the top of every review iteration, Owl runs the configured command in
  each repo's root directory. A repo with no command configured is silently
  skipped — not every repo needs a deterministic suite.
- If any suite exits non-zero, the last 200 lines of its combined
  stdout/stderr are appended to that round's `combined_review_<i>.txt` under
  a **Deterministic test failures** heading and handed to the fix agent with
  the reviewer comments. The fix prompt is instructed to treat failing tests
  as non-negotiable and to investigate root cause rather than delete or skip
  tests.
- The LGTM early-exit is gated on tests passing: Owl will not bail out of the
  review loop while any configured suite is red, even if both reviewers
  return `LGTM`.
- Per-repo logs land in `.work/<plan>/tests_<repo>_<round>.log` and the
  aggregated failure summary in `.work/<plan>/tests_<round>.txt`.

Keep the command **fast and deterministic** — it runs on every iteration. Put
slow end-to-end or real-LLM suites behind a separate command/marker so they
don't burn budget in the Owl loop. For example, the saudade repo separates
cheap tests (`tests/`) from LLM-backed tests (`tests/e2e/`) and configures
only the cheap path here.

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
./src/owl.sh

# Start with low-priority plans skipped (daytime token-saving mode)
./src/owl.sh --skip-low-priority

# Dry-run: parse a plan and print what the agent would do (no LLM calls, no git)
./src/owl.sh --validate plan/001-my-task.md

# Watch progress
tail -f agent.log

# Stop
Ctrl+C or kill the process
```

The `--skip-low-priority` flag and `OWL_SKIP_LOW_PRIORITY=1` env var are
equivalent. A typical workflow: run with `--skip-low-priority` during the
day while you iterate on high-value work, then restart without the flag at
night so the queued low-priority plans drain overnight.

### `--validate` output example

```
$ OWL_TARGET_REPOS=myrepo ./src/owl.sh --validate plan/042-add-login.md
Plan file: /path/to/owl/plan/042-add-login.md

Frontmatter:
  review-rounds : 2
  priority      : normal
  base-branch   : (none)

Target branch: owl/042-add-login

Target repos:
  - myrepo (/path/to/myrepo)

validation OK -- run without --validate to execute
```

`--validate` never acquires the lock, so it can run safely while the agent is
already running.

## Using the plan-authoring skill

A Claude Code skill at `skills/owl-plan-author/SKILL.md` walks Claude through every step of drafting a new Owl plan — numbering, structure, anchoring, frontmatter, and queue hygiene. It auto-activates when you say things like "queue an Owl plan" or "add this to the Owl queue".

**Install (one-time, run after cloning or pulling this change):**

```sh
# Symlink — future edits to the repo file propagate automatically (recommended)
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/owl-plan-author" ~/.claude/skills/owl-plan-author
```

Alternative (copy — snapshot, won't track future edits):

```sh
mkdir -p ~/.claude/skills/owl-plan-author
cp skills/owl-plan-author/SKILL.md ~/.claude/skills/owl-plan-author/SKILL.md
```

After installing, **restart Claude Code** to pick up the skill (skills are scanned at session start and don't hot-reload).

## Notes

- Claude-backed roles use `claude --dangerously-skip-permissions` — review plans before enabling them
- Codex-backed roles use `codex exec --full-auto --skip-git-repo-check`
- All logs go to `agent.log`
- Work directories (`.work/`) contain per-plan execution logs, review files, and commit manifests. When a plan aborts mid-review, a `pending_status` file is written with the failed iteration, branch name, and reason so the next cycle can resume it cleanly.

## License

MIT — see [LICENSE](LICENSE) for details.
