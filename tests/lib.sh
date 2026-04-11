#!/bin/bash
# Shared helpers for owl's bash test suite.
#
# Tests source owl.sh under a fake PROJECT_DIR so internal functions
# (find_target_repos, run_deterministic_tests, run_review_loop, ...) can be
# exercised in isolation without running the main loop or touching real
# repos. owl.sh has a `BASH_SOURCE[0] == $0` guard around its main section,
# so sourcing does not call acquire_lock or the poll loop.

# Resolve owl.sh relative to this file's directory.
TESTS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWL_ROOT="$(cd "$TESTS_LIB_DIR/.." && pwd)"
OWL_SCRIPT="$OWL_ROOT/src/owl.sh"

# -----------------------------------------------------------------------------
# Fixture setup
# -----------------------------------------------------------------------------

# Usage: `setup_fake_project "$tmp_root" repo-a repo-b`
# Creates `$tmp_root/project/<repo>` as a real git repo with one empty commit.
# Exports OWL_TARGET_REPOS (space-separated repo names) and FAKE_PROJECT_DIR
# (absolute path to the project root). Call directly — do NOT invoke via
# `$(setup_fake_project ...)`, because command substitution runs the function
# in a subshell and export-into-subshell does not propagate to the caller.
# The caller is responsible for reassigning PROJECT_DIR after sourcing owl.sh,
# since owl.sh computes it at source time.
setup_fake_project() {
  local tmp_root="$1"
  shift
  FAKE_PROJECT_DIR="$tmp_root/project"
  export FAKE_PROJECT_DIR
  mkdir -p "$FAKE_PROJECT_DIR"
  local names=""
  for repo_name in "$@"; do
    local repo_root="$FAKE_PROJECT_DIR/$repo_name"
    mkdir -p "$repo_root"
    git -C "$repo_root" init -q -b main
    git -C "$repo_root" -c user.email=owl@test -c user.name=owl commit -q --allow-empty -m "init"
    names="$names $repo_name"
  done
  export OWL_TARGET_REPOS="${names# }"
}

# Source owl.sh with the main loop skipped, then rewire the globals it
# computed at source time to point at the fixture sandbox instead of the
# user's real environment.
#
# owl.sh sources `.env.local` during its top-of-file setup, which can
# overwrite any OWL_TARGET_REPOS the test exported. To defeat that we
# reassign TARGET_REPOS (and other path globals) AFTER sourcing, using
# FAKE_PROJECT_DIR that `setup_fake_project` left behind.
#
# Callers still need to set PLAN_WORK_DIR / LOG_FILE to whatever temp
# paths they want to inspect.
source_owl() {
  # Skip .env.local so tests aren't at the mercy of the developer's local
  # config (OWL_REVIEWER2_PROVIDER=none in .env.local was silently disabling
  # slot 2 in tests that explicitly exported it as claude).
  export OWL_SKIP_ENV_LOCAL=1

  local fake_target_repos="${OWL_TARGET_REPOS:-}"
  # shellcheck disable=SC1090
  . "$OWL_SCRIPT"

  # Re-point the script at the fixture. These variables were computed at
  # source time from the real parent directory — reassigning them is
  # necessary so find_target_repos enumerates the fake repos.
  if [ -n "${FAKE_PROJECT_DIR:-}" ]; then
    PROJECT_DIR="$FAKE_PROJECT_DIR"
  fi
  if [ -n "$fake_target_repos" ]; then
    TARGET_REPOS="$fake_target_repos"
  fi
  # Silence log() in tests — it normally tees to stdout AND a file. We only
  # care about the file (callers can cat it for debugging). This keeps test
  # output terse.
  log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "${LOG_FILE:-/dev/null}"
  }
}

# -----------------------------------------------------------------------------
# Assertion helpers
# -----------------------------------------------------------------------------

# Counter tracked by run_tests.sh via the TESTS_PASSED / TESTS_FAILED env
# vars. When sourced directly by a single test file we just log to stderr.
assert_eq() {
  local expected="$1"
  local actual="$2"
  local msg="${3:-assert_eq failed}"
  if [ "$expected" = "$actual" ]; then
    return 0
  fi
  echo "  FAIL: $msg — expected='$expected' actual='$actual'" >&2
  return 1
}

assert_file_exists() {
  local path="$1"
  local msg="${2:-file should exist}"
  if [ -f "$path" ]; then
    return 0
  fi
  echo "  FAIL: $msg — not found: $path" >&2
  return 1
}

assert_file_not_exists() {
  local path="$1"
  local msg="${2:-file should not exist}"
  if [ ! -e "$path" ]; then
    return 0
  fi
  echo "  FAIL: $msg — unexpectedly present: $path" >&2
  return 1
}

assert_grep() {
  local pattern="$1"
  local file="$2"
  local msg="${3:-pattern should be present}"
  if grep -q -- "$pattern" "$file" 2>/dev/null; then
    return 0
  fi
  echo "  FAIL: $msg — pattern '$pattern' not found in $file" >&2
  return 1
}

assert_not_grep() {
  local pattern="$1"
  local file="$2"
  local msg="${3:-pattern should not be present}"
  if [ ! -f "$file" ] || ! grep -q -- "$pattern" "$file" 2>/dev/null; then
    return 0
  fi
  echo "  FAIL: $msg — pattern '$pattern' unexpectedly found in $file" >&2
  return 1
}
