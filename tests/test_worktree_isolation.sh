#!/bin/bash
# Regression tests for deterministic per-plan worktree execution.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TMPDIR=$(mktemp -d -t owl-worktree.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

setup_fake_project "$TMPDIR" repo-a repo-b

export OWL_IMPL_PROVIDER=none
export OWL_FIX_PROVIDER=none
export OWL_REVIEWER1_PROVIDER=none
export OWL_REVIEWER2_PROVIDER=none

source_owl

LOG_FILE="$TMPDIR/agent.log"
WORK_DIR="$TMPDIR/.work"
PLAN_WORK_DIR="$TMPDIR/plan_work"
PLAN_NAME="123-worktree-isolation"
mkdir -p "$WORK_DIR" "$PLAN_WORK_DIR"

SOURCE_REPO="$FAKE_PROJECT_DIR/repo-a"
WORKTREE_REPO="$WORK_DIR/worktrees/$PLAN_NAME/repo-a"

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

case_worktree_isolates_dirty_source_repo() {
  echo "do not touch" > "$SOURCE_REPO/source-dirty.txt"

  ensure_plan_workspace "$PLAN_NAME" "$PLAN_WORK_DIR" || return 1

  assert_file_exists "$WORKTREE_REPO/.git" "worktree uses .git file layout" || return 1
  if [ -d "$WORKTREE_REPO/.git" ]; then
    echo "  FAIL: worktree .git should be a file, not a directory" >&2
    return 1
  fi
  if ! repo_has_local_changes "$SOURCE_REPO"; then
    echo "  FAIL: source repo should remain dirty after worktree creation" >&2
    return 1
  fi
  assert_file_exists "$SOURCE_REPO/source-dirty.txt" "source repo file preserved" || return 1

  echo "generated in worktree" > "$WORKTREE_REPO/worktree-only.txt"
  if ! repo_has_local_changes "$WORKTREE_REPO"; then
    echo "  FAIL: worktree repo should show local changes before reset" >&2
    return 1
  fi

  reset_all_repos_to_base "" "1"

  assert_file_not_exists "$WORKTREE_REPO/worktree-only.txt" "reset drops Owl-managed worktree changes" || return 1
  assert_file_exists "$SOURCE_REPO/source-dirty.txt" "reset does not touch source repo" || return 1
  if ! repo_has_local_changes "$SOURCE_REPO"; then
    echo "  FAIL: source repo should still be dirty after worktree reset" >&2
    return 1
  fi
  if git -C "$WORKTREE_REPO" symbolic-ref -q HEAD >/dev/null 2>&1; then
    echo "  FAIL: worktree should be detached after reset" >&2
    return 1
  fi

  cleanup_plan_workspace "$PLAN_NAME" "$PLAN_WORK_DIR"

  if [ -e "$WORKTREE_REPO" ]; then
    echo "  FAIL: cleanup should remove the worktree repo" >&2
    return 1
  fi
  assert_file_exists "$SOURCE_REPO/source-dirty.txt" "cleanup leaves source repo alone" || return 1
  return 0
}

echo "test_worktree_isolation:"
run_case "worktree execution isolates dirty source repos" case_worktree_isolates_dirty_source_repo

if [ "$failures" -ne 0 ]; then
  echo "test_worktree_isolation: $failures FAILED"
  exit 1
fi
echo "test_worktree_isolation: all pass"
exit 0
