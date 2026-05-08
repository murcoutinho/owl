---
name: owl-config
description: Use this skill when the user asks to "configure Owl", "set up Owl", "add a repo to Owl", "change Owl model", "set Owl reviewer", or any variation of configuring Owl's environment, target repos, providers, models, or test commands.
---

# Owl Configuration

Follow this guide to configure Owl. All configuration lives in `.env.local` at the Owl repo root (gitignored).

---

## Step 1 — Locate Owl and understand the directory model

Owl expects target repos to be **sibling directories** of the `owl/` folder. The script computes:

```
PROJECT_DIR = owl/../../
```

So if Owl is at `/home/user/projects/owl/`, it looks for repos at `/home/user/projects/<repo-name>/`.

**Check the current Owl location:**

```sh
ls -la "$(dirname "$(dirname "$(readlink -f "$(which owl 2>/dev/null || echo src/owl.sh)")")")"
```

Or simply check where the `.env.local` lives:

```sh
find ~ -path "*/owl/.env.local" 2>/dev/null
```

---

## Step 2 — Link target repos

If a target repo is not already a sibling of `owl/`, create a symlink. This is the recommended approach (no copies, no moves).

**Before creating a symlink:**

1. Identify the Owl parent directory: the directory that contains the `owl/` folder.
2. Identify the real path of the target repo.
3. Verify the symlink name matches what you'll put in `OWL_TARGET_REPOS`.

**Create the symlink:**

```sh
ln -s /real/path/to/my-repo /path/to/owl-parent/my-repo
```

**Verify:**

```sh
ls -la /path/to/owl-parent/my-repo
git -C /path/to/owl-parent/my-repo rev-parse --is-inside-work-tree
```

Both should succeed. The symlink is transparent: Owl, git, and all other tools follow it automatically.

**Multiple repos:** repeat for each repo. All symlinks go in the same parent directory as `owl/`.

---

## Step 3 — Write `.env.local`

Create or edit `owl/.env.local`. This file is gitignored and machine-specific.

### Required

```sh
OWL_TARGET_REPOS="repo-name-1 repo-name-2"
```

Space-separated list of directory names (not paths) that exist as siblings of `owl/` (real directories or symlinks).

### Provider and model configuration

Owl has three roles: **implementation/fix** (writes code), **reviewer 1**, and **reviewer 2**. Each role has a provider (`claude` or `codex`) and a model.

```sh
# Implementation and fix
OWL_IMPL_PROVIDER=claude
OWL_IMPL_MODEL=claude-opus-4-7
OWL_FIX_PROVIDER=claude
OWL_FIX_MODEL=claude-opus-4-7

# Reviewer 1
OWL_REVIEWER1_PROVIDER=claude
OWL_REVIEWER1_MODEL=claude-opus-4-6
OWL_REVIEWER1_LABEL="Claude Opus 4.6"

# Reviewer 2
OWL_REVIEWER2_PROVIDER=claude
OWL_REVIEWER2_MODEL=claude-sonnet-4-6
OWL_REVIEWER2_LABEL="Claude Sonnet 4.6"
```

To disable a reviewer slot, set its provider to `none`:

```sh
OWL_REVIEWER2_PROVIDER=none
OWL_REVIEWER2_MODEL=none
```

### Optional: test commands

Per-repo test commands run automatically at the top of every review round. Replace hyphens in repo names with underscores for the env var name.

```sh
OWL_TEST_CMD_my_repo="npm test --silent"
OWL_TEST_SETUP_my_repo="npm ci"
```

### Optional: review and timing

```sh
OWL_REVIEW_MODE=parallel          # or "sequential"
OWL_LLM_TIMEOUT=2400              # seconds per LLM call (default 40 min)
OWL_POLL_INTERVAL_SECONDS=600     # seconds between queue polls (default 10 min)
```

---

## Step 4 — Verify

Run the doctor command from the Owl directory:

```sh
cd /path/to/owl && ./src/owl.sh --doctor
```

This checks:
- CLI tools are installed and authenticated (claude, codex, gh)
- Target repos are found and are valid git repos
- No LLM calls are made

---

## Step 5 — Install skills

Owl ships a plan-authoring skill. Symlink it into your Claude Code skills directory:

```sh
mkdir -p ~/.claude/skills
ln -s "$(cd /path/to/owl && pwd)/skills/owl-plan-author" ~/.claude/skills/owl-plan-author
ln -s "$(cd /path/to/owl && pwd)/skills/owl-config" ~/.claude/skills/owl-config
```

Restart Claude Code after installing skills.

---

## Reference: all environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OWL_TARGET_REPOS` | (required) | Space-separated sibling repo directory names |
| `OWL_IMPL_PROVIDER` | `claude` | Implementation provider: `claude` or `codex` |
| `OWL_IMPL_MODEL` | `claude-sonnet-4-6` | Implementation model |
| `OWL_FIX_PROVIDER` | `$OWL_IMPL_PROVIDER` | Fix-phase provider |
| `OWL_FIX_MODEL` | `$OWL_IMPL_MODEL` | Fix-phase model |
| `OWL_REVIEWER1_PROVIDER` | `claude` | Reviewer 1 provider: `claude`, `codex`, or `none` |
| `OWL_REVIEWER1_MODEL` | `claude-sonnet-4-6` | Reviewer 1 model |
| `OWL_REVIEWER1_LABEL` | `Claude Code 1` | Reviewer 1 label in logs |
| `OWL_REVIEWER2_PROVIDER` | `claude` | Reviewer 2 provider: `claude`, `codex`, or `none` |
| `OWL_REVIEWER2_MODEL` | `claude-sonnet-4-6` | Reviewer 2 model |
| `OWL_REVIEWER2_LABEL` | `Claude Code 2` | Reviewer 2 label in logs |
| `OWL_REVIEW_MODE` | `parallel` | `parallel` or `sequential` |
| `OWL_SKIP_LOW_PRIORITY` | `0` | Skip `priority: low` plans when `1` |
| `OWL_TEST_CMD_<repo>` | (unset) | Test command for a repo (underscores for hyphens) |
| `OWL_TEST_SETUP_<repo>` | (unset) | Setup command before tests (e.g. `npm ci`) |
| `OWL_LLM_TIMEOUT` | `2400` | Max seconds per LLM subprocess |
| `OWL_POLL_INTERVAL_SECONDS` | `600` | Seconds between queue polls |
| `REVIEW_ITERATIONS` | `2` | Default review rounds if plan omits `review-rounds` |
| `RETRY_WAIT` | `600` | Seconds between rate-limit retries |
| `MAX_RETRIES` | `50` | Max retry attempts |
