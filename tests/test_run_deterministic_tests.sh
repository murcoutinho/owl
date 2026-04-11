#!/bin/bash
# Tests for run_deterministic_tests (the test gate from commit 1e1a4c1).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TMPDIR=$(mktemp -d -t owl-test.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

setup_fake_project "$TMPDIR" repo-a repo-b

# owl.sh needs these set at source time.
export OWL_IMPL_PROVIDER=none
export OWL_FIX_PROVIDER=none
export OWL_REVIEWER1_PROVIDER=none
export OWL_REVIEWER2_PROVIDER=none

source_owl

# Point owl at the fixture project instead of its real parent dir.
PROJECT_DIR="$FAKE_PROJECT_DIR"
LOG_FILE="$TMPDIR/agent.log"
PLAN_WORK_DIR="$TMPDIR/plan_work"
mkdir -p "$PLAN_WORK_DIR"

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

# --- Case 1: no command configured → rc=0, empty summary ---
case_no_commands_configured() {
  unset OWL_TEST_CMD_repo_a OWL_TEST_CMD_repo_b 2>/dev/null || true
  local rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "1" || rc=$?
  assert_eq 0 "$rc" "no commands → rc=0" || return 1
  if [ -s "$PLAN_WORK_DIR/tests_1.txt" ]; then
    echo "  FAIL: summary should be empty when nothing configured" >&2
    return 1
  fi
  return 0
}

# --- Case 2: all configured suites pass → rc=0, empty summary ---
case_all_pass() {
  export OWL_TEST_CMD_repo_a="true"
  export OWL_TEST_CMD_repo_b="true"
  local rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "2" || rc=$?
  assert_eq 0 "$rc" "all-pass → rc=0" || return 1
  if [ -s "$PLAN_WORK_DIR/tests_2.txt" ]; then
    echo "  FAIL: summary should be empty when all pass" >&2
    return 1
  fi
  return 0
}

# --- Case 3: one suite fails → rc=1, summary names the failing repo only ---
case_one_fails() {
  export OWL_TEST_CMD_repo_a="true"
  export OWL_TEST_CMD_repo_b='echo "boom suite output"; exit 1'
  local rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "3" || rc=$?
  assert_eq 1 "$rc" "one failure → rc=1" || return 1
  assert_grep "repo-b" "$PLAN_WORK_DIR/tests_3.txt" "summary mentions failing repo" || return 1
  assert_grep "boom suite output" "$PLAN_WORK_DIR/tests_3.txt" "summary captures suite stdout" || return 1
  assert_grep "exit=1" "$PLAN_WORK_DIR/tests_3.txt" "summary records exit code" || return 1
  # Passing repo should NOT appear in the summary section headers.
  assert_not_grep "^### repo-a" "$PLAN_WORK_DIR/tests_3.txt" "passing repo omitted from summary" || return 1
  # Per-repo log file should be on disk for operator inspection.
  assert_file_exists "$PLAN_WORK_DIR/tests_repo-b_3.log" "per-repo log written" || return 1
  return 0
}

# --- Case 4: hyphen→underscore variable name mapping ---
# repo-a must be looked up via OWL_TEST_CMD_repo_a (not OWL_TEST_CMD_repo-a,
# which is not even a legal bash variable name).
case_hyphen_to_underscore_mapping() {
  unset OWL_TEST_CMD_repo_a OWL_TEST_CMD_repo_b 2>/dev/null || true
  export OWL_TEST_CMD_repo_a="exit 42"
  local rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "4" || rc=$?
  assert_eq 1 "$rc" "repo-a failure triggers via underscore var" || return 1
  assert_grep "exit=42" "$PLAN_WORK_DIR/tests_4.txt" "exact exit code propagated" || return 1
  return 0
}

# --- Case 5: summary is truncated/rewritten on each call ---
# The : > "$summary_file" at the top of run_deterministic_tests means a
# later successful run must wipe an earlier failure summary — otherwise the
# review loop would re-feed stale failures to the fix agent.
case_summary_rewritten_per_call() {
  # First call: fail and leave content.
  export OWL_TEST_CMD_repo_a='echo stale; exit 1'
  local rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "5" || rc=$?
  assert_eq 1 "$rc" "stale run fails" || return 1
  assert_grep "stale" "$PLAN_WORK_DIR/tests_5.txt" "stale content present" || return 1

  # Second call on same round: pass this time; summary must be emptied.
  export OWL_TEST_CMD_repo_a="true"
  rc=0
  run_deterministic_tests "$PLAN_WORK_DIR" "5" || rc=$?
  assert_eq 0 "$rc" "rerun passes" || return 1
  if [ -s "$PLAN_WORK_DIR/tests_5.txt" ]; then
    echo "  FAIL: summary should be emptied after a clean rerun" >&2
    return 1
  fi
  return 0
}

echo "test_run_deterministic_tests:"
run_case "no commands configured → rc=0, empty summary" case_no_commands_configured
run_case "all pass → rc=0, empty summary" case_all_pass
run_case "one fails → rc=1, summary scoped to failing repo" case_one_fails
run_case "hyphen→underscore variable name mapping" case_hyphen_to_underscore_mapping
run_case "summary file is rewritten on each call" case_summary_rewritten_per_call

if [ "$failures" -ne 0 ]; then
  echo "test_run_deterministic_tests: $failures FAILED"
  exit 1
fi
echo "test_run_deterministic_tests: all pass"
exit 0
