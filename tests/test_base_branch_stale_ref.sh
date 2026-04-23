#!/bin/bash
# Regression test for the stale-ref bug in reset_all_repos_to_base and
# push_and_open_prs.
#
# Bug: when a base plan (e.g. 031) merged and its upstream branch was
# auto-deleted, but the Owl host still had a cached
# `refs/remotes/origin/owl/031-...` ref from when it originally ran 031,
# `rev-parse --verify` would still succeed against the cached ref and Owl
# would use it as the base instead of falling back to main. The dependent
# plan would then run on yesterday's base and its PR creation would target
# a deleted branch.
#
# Fix (commit paired with this test): require the explicit `git fetch
# origin <base>` to exit 0 in addition to the rev-parse check. A failed
# fetch means the branch really is gone on origin, regardless of what the
# stale cached ref says — fall back to main and prune the stale ref.
#
# Test setup builds a fake bare remote, clones a real working repo from it,
# creates a feature branch on the bare remote, fetches it into the clone
# (populating the remote-tracking ref), then DELETES the branch from the
# bare remote without pruning on the clone side. That exactly reproduces
# the stale-ref condition. We then call reset_all_repos_to_base and assert
# the function falls back to main.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TMPDIR=$(mktemp -d -t owl-stale.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

# --- Build a fake origin + working clone ---
BARE_REMOTE="$TMPDIR/bare-origin.git"
git init -q --bare -b main "$BARE_REMOTE"

# Seed the bare remote with an initial commit on main so clones are valid.
SEED_REPO="$TMPDIR/seed"
git init -q -b main "$SEED_REPO"
git -C "$SEED_REPO" -c user.email=seed@test -c user.name=seed commit -q --allow-empty -m "init"
git -C "$SEED_REPO" remote add origin "$BARE_REMOTE"
git -C "$SEED_REPO" push -q origin main

# Create a feature branch on the bare remote. This is our stand-in for the
# ancestor plan's branch (e.g. owl/031-...).
GHOST_BRANCH="owl/031-ghost-ancestor"
git -C "$SEED_REPO" checkout -q -b "$GHOST_BRANCH"
git -C "$SEED_REPO" -c user.email=seed@test -c user.name=seed commit -q --allow-empty -m "ghost ancestor content"
git -C "$SEED_REPO" push -q origin "$GHOST_BRANCH"
git -C "$SEED_REPO" checkout -q main

# Build the Owl-target layout: $FAKE_PROJECT_DIR/ghost-repo
PROJECT_DIR_OVERRIDE="$TMPDIR/project"
mkdir -p "$PROJECT_DIR_OVERRIDE"
git clone -q "$BARE_REMOTE" "$PROJECT_DIR_OVERRIDE/ghost-repo"
# Match setup_fake_project's committer identity so future commits do not
# need global git config.
git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" config user.email owl@test
git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" config user.name owl

# Populate the clone's cache of the ghost branch. This is what the real
# Owl host would have after running plan 031 — the remote-tracking ref
# points at the ghost branch's tip.
git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" fetch -q origin "$GHOST_BRANCH"

# Sanity check: the cached ref exists right now.
if ! git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse --verify "refs/remotes/origin/$GHOST_BRANCH" >/dev/null 2>&1; then
  echo "FIXTURE FAIL: cached remote-tracking ref was not populated after initial fetch" >&2
  exit 1
fi

# Now delete the branch on the bare remote (simulates the auto-delete
# after 031's PR merges). Crucially, do NOT prune on the clone side.
git -C "$SEED_REPO" push -q origin --delete "$GHOST_BRANCH"

# Confirm the stale-ref condition: local clone still has the remote-
# tracking ref even though the branch is gone upstream.
if ! git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse --verify "refs/remotes/origin/$GHOST_BRANCH" >/dev/null 2>&1; then
  echo "FIXTURE FAIL: cached remote-tracking ref was pruned unexpectedly — test cannot exercise the bug" >&2
  exit 1
fi

# --- Source owl.sh under the fixture ---
export FAKE_PROJECT_DIR="$PROJECT_DIR_OVERRIDE"
export OWL_TARGET_REPOS="ghost-repo"
export OWL_IMPL_PROVIDER=none
export OWL_FIX_PROVIDER=none
export OWL_REVIEWER1_PROVIDER=none
export OWL_REVIEWER2_PROVIDER=none

source_owl

LOG_FILE="$TMPDIR/agent.log"
: > "$LOG_FILE"

failures=0
run_case() {
  local name="$1"
  shift
  if "$@"; then
    echo "  ok — $name"
  else
    echo "  FAIL — $name"
    failures=$((failures + 1))
  fi
}

# --- Case 1: reset_all_repos_to_base with a deleted base falls back to main ---
# Before the fix: target_branch would become $GHOST_BRANCH because the
# cached ref still verifies. After the fix: the explicit fetch fails, the
# cached ref is irrelevant, and target_branch stays "main".
case_fallback_to_main_when_base_deleted() {
  reset_all_repos_to_base "$GHOST_BRANCH"

  local actual_head expected_head current_branch
  actual_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse HEAD)
  expected_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse main)
  current_branch=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" branch --show-current)
  if [ "$actual_head" != "$expected_head" ]; then
    echo "  FAIL: ghost-repo HEAD did not land on main after fallback" >&2
    return 1
  fi
  if [ -n "$current_branch" ]; then
    echo "  FAIL: ghost-repo should be detached after fallback, got branch '$current_branch'" >&2
    return 1
  fi

  # The log should have the fallback message, not the "using base branch"
  # message.
  if ! grep -q "base branch '$GHOST_BRANCH' not on origin — falling back to main" "$LOG_FILE"; then
    echo "  FAIL: fallback message missing from $LOG_FILE" >&2
    return 1
  fi
  if grep -q "using base branch '$GHOST_BRANCH' from origin" "$LOG_FILE"; then
    echo "  FAIL: owl.sh still claimed to be using the ghost branch" >&2
    return 1
  fi

  # The stale cached ref should have been actively pruned by the fix, so
  # any later operation on this repo cannot resurrect it.
  if git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse --verify "refs/remotes/origin/$GHOST_BRANCH" >/dev/null 2>&1; then
    echo "  FAIL: stale remote-tracking ref was not pruned after fallback" >&2
    return 1
  fi

  return 0
}

# --- Case 2: reset_all_repos_to_base with a live base still uses it ---
# Guardrail: the fix must not regress the happy path. Create a fresh
# feature branch on the bare remote, fetch it, and confirm owl.sh uses it.
case_live_base_still_used() {
  local LIVE_BRANCH="owl/042-live-ancestor"
  git -C "$SEED_REPO" checkout -q -b "$LIVE_BRANCH"
  git -C "$SEED_REPO" -c user.email=seed@test -c user.name=seed commit -q --allow-empty -m "live ancestor"
  git -C "$SEED_REPO" push -q origin "$LIVE_BRANCH"
  git -C "$SEED_REPO" checkout -q main

  : > "$LOG_FILE"
  reset_all_repos_to_base "$LIVE_BRANCH"

  local actual_head expected_head current_branch
  actual_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse HEAD)
  expected_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse "refs/remotes/origin/$LIVE_BRANCH")
  current_branch=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" branch --show-current)
  if [ "$actual_head" != "$expected_head" ]; then
    echo "  FAIL: expected ghost-repo detached at '$LIVE_BRANCH'" >&2
    return 1
  fi
  if [ -n "$current_branch" ]; then
    echo "  FAIL: expected detached HEAD for live base, got branch '$current_branch'" >&2
    return 1
  fi

  if ! grep -q "using base branch '$LIVE_BRANCH' from origin" "$LOG_FILE"; then
    echo "  FAIL: expected 'using base branch' log line" >&2
    return 1
  fi
  if grep -q "falling back to main" "$LOG_FILE"; then
    echo "  FAIL: owl.sh unnecessarily fell back to main for a live branch" >&2
    return 1
  fi

  return 0
}

# --- Case 3: empty base_branch resets to main as before ---
case_empty_base_resets_to_main() {
  : > "$LOG_FILE"
  # Start from a non-main branch to make the assertion meaningful.
  git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" checkout -q -b some-local-branch 2>/dev/null || \
    git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" checkout -q some-local-branch

  reset_all_repos_to_base ""

  local actual_head expected_head current_branch
  actual_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse HEAD)
  expected_head=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" rev-parse main)
  current_branch=$(git -C "$PROJECT_DIR_OVERRIDE/ghost-repo" branch --show-current)
  if [ "$actual_head" != "$expected_head" ]; then
    echo "  FAIL: empty base should detach at main" >&2
    return 1
  fi
  if [ -n "$current_branch" ]; then
    echo "  FAIL: empty base should leave detached HEAD, got '$current_branch'" >&2
    return 1
  fi
  return 0
}

echo "test_base_branch_stale_ref:"
run_case "deleted base branch falls back to main + prunes stale ref" case_fallback_to_main_when_base_deleted
run_case "live base branch is still used" case_live_base_still_used
run_case "empty base resets to main" case_empty_base_resets_to_main

if [ "$failures" -ne 0 ]; then
  echo "test_base_branch_stale_ref: $failures FAILED"
  exit 1
fi
echo "test_base_branch_stale_ref: all pass"
exit 0
