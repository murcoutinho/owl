#!/bin/bash
# Regression test for commit 3400a6a — "Preserve pending state when review
# loop aborts on LLM failure".
#
# Before 3400a6a, if every reviewer in an iteration hit MAX_RETRIES rate-limit
# exhaustion, `run_review_loop` logged "skipped" and fell through to the
# happy-path push/done/cleanup. Plan got marked done, plan file deleted,
# pending marker wiped — a whole plan silently shipped with zero successful
# review rounds. Same failure mode for the fix-phase LLM call.
#
# This test stubs `run_llm` to always fail (simulating reviewer/fix rate-
# limit exhaustion) and asserts the post-fix invariants:
#   1. run_review_loop returns non-zero
#   2. $plan_work_dir/pending_status is written with abort context
#   3. The plan file is NOT deleted
#   4. switch_all_to_main is called so the operator's tree isn't stuck
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TMPDIR=$(mktemp -d -t owl-abort.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

setup_fake_project "$TMPDIR" fake-repo

# Reviewer providers must be "real" (not "none") so both get enabled and the
# "all reviewers failed" branch is reachable. We'll stub run_llm to never
# actually call claude/codex.
export OWL_IMPL_PROVIDER=claude
export OWL_FIX_PROVIDER=claude
export OWL_REVIEWER1_PROVIDER=claude
export OWL_REVIEWER2_PROVIDER=claude
# Sequential reviewers so the counter-based stub in case 2 is race-free.
export OWL_REVIEW_MODE=sequential

source_owl

LOG_FILE="$TMPDIR/agent.log"
PLAN_DIR="$TMPDIR/plan"
WORK_DIR="$TMPDIR/.work"
mkdir -p "$PLAN_DIR" "$WORK_DIR"

# Fake plan file. Content is read by strip_plan_frontmatter for the review
# prompt; a one-liner is enough.
PLAN_FILE="$PLAN_DIR/099-abort-regression.md"
cat > "$PLAN_FILE" <<'EOF'
# Abort regression fixture

A deliberately minimal plan used only to drive run_review_loop to its
reviewer/fix branches.
EOF

PLAN_NAME="099-abort-regression"
PLAN_WORK_DIR="$WORK_DIR/$PLAN_NAME"
mkdir -p "$PLAN_WORK_DIR"

# Minimal plan work dir state. run_review_loop reads:
#   - $plan_work_dir/branch   (branch name; checkout is skipped if branch
#     doesn't exist in the repo, which is fine for this test)
#   - $plan_work_dir/review_input_$i.tsv  (manifest of repos+commit ranges)
#   - $plan_work_dir/state    (reviews_done, optional)
echo "owl/$PLAN_NAME" > "$PLAN_WORK_DIR/branch"
# Write the actual HEAD SHA into the manifest so the resume-phase drift
# check (added upstream) sees a matching head and does not bail out.
FAKE_REPO_HEAD="$(git -C "$FAKE_PROJECT_DIR/fake-repo" rev-parse HEAD)"
printf 'fake-repo\t%s\tNONE\t%s\n' "$FAKE_PROJECT_DIR/fake-repo" "$FAKE_REPO_HEAD" > "$PLAN_WORK_DIR/review_input_1.tsv"

# ---- Stubs ---------------------------------------------------------------

# run_llm is the function all reviewer/fix phases route through. Returning
# non-zero here mimics `retry_on_limit` giving up after MAX_RETRIES. We set
# a rate-limit-flavoured RETRY_OUTPUT so the improved abort logging captures
# something meaningful for the operator.
run_llm() {
  RETRY_OUTPUT="anthropic: error 429 rate_limit_exceeded (stub)"
  return 1
}

# switch_all_to_main does real `git checkout main` in every target repo.
# In the test we only need to confirm it was called — not to actually
# touch the fake git worktrees.
SWITCH_CALLS_LOG="$TMPDIR/switch_calls.log"
: > "$SWITCH_CALLS_LOG"
switch_all_to_main() {
  echo "switch_all_to_main called at $(date '+%s')" >> "$SWITCH_CALLS_LOG"
}

# ---- Test cases ----------------------------------------------------------

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

# --- Case 1: reviewer-phase abort preserves state ---
# Both reviewers fail → had_unrecoverable_llm_failure → break → bail path.
case_reviewer_abort_preserves_pending() {
  # Fresh state — no prior pending_status, no completed rounds.
  rm -f "$PLAN_WORK_DIR/pending_status" "$PLAN_WORK_DIR/state" \
        "$PLAN_WORK_DIR/combined_review_"*.txt \
        "$PLAN_WORK_DIR/reviewer1_"*.txt "$PLAN_WORK_DIR/reviewer2_"*.txt \
        "$PLAN_WORK_DIR/fixes_"*.log 2>/dev/null || true
  : > "$SWITCH_CALLS_LOG"

  local rc=0
  run_review_loop "$PLAN_FILE" "$PLAN_NAME" "$PLAN_WORK_DIR" || rc=$?

  assert_eq 1 "$rc" "run_review_loop returns non-zero on reviewer abort" || return 1
  assert_file_exists "$PLAN_WORK_DIR/pending_status" "pending_status file persisted" || return 1
  assert_file_exists "$PLAN_FILE" "plan file NOT deleted" || return 1
  assert_grep "^plan_name=$PLAN_NAME$" "$PLAN_WORK_DIR/pending_status" "pending_status records plan_name" || return 1
  assert_grep "^branch_name=owl/$PLAN_NAME$" "$PLAN_WORK_DIR/pending_status" "pending_status records branch_name" || return 1
  assert_grep "^failed_iteration=1$" "$PLAN_WORK_DIR/pending_status" "pending_status records failed_iteration" || return 1
  assert_grep "^reason=all reviewers failed" "$PLAN_WORK_DIR/pending_status" "pending_status records reviewer abort reason" || return 1
  assert_grep "switch_all_to_main called" "$SWITCH_CALLS_LOG" "switch_all_to_main was invoked" || return 1
  # Improved abort logging should surface the stubbed RETRY_OUTPUT tail so
  # operators can distinguish rate-limit exhaustion from tool crashes.
  assert_grep "tail:.*rate_limit_exceeded" "$LOG_FILE" "abort log includes output tail" || return 1
  return 0
}

# --- Case 2: fix-phase abort preserves state ---
# Reviewers succeed with non-LGTM output, triggering the fix phase. The
# fix-phase run_llm then fails → abort path fires with a different reason.
case_fix_abort_preserves_pending() {
  rm -f "$PLAN_WORK_DIR/pending_status" "$PLAN_WORK_DIR/state" \
        "$PLAN_WORK_DIR/combined_review_"*.txt \
        "$PLAN_WORK_DIR/reviewer1_"*.txt "$PLAN_WORK_DIR/reviewer2_"*.txt \
        "$PLAN_WORK_DIR/fixes_"*.log 2>/dev/null || true
  : > "$SWITCH_CALLS_LOG"

  # First, stub run_llm so reviewers succeed with non-LGTM content (forcing
  # the loop to enter the fix phase) but the fix call still fails. We do
  # this by counting invocations — reviewers 1 and 2 pass, fix (call 3)
  # fails.
  local rll_counter_file="$TMPDIR/run_llm_count"
  echo 0 > "$rll_counter_file"
  run_llm() {
    local n
    n=$(cat "$rll_counter_file")
    n=$((n + 1))
    echo "$n" > "$rll_counter_file"
    if [ "$n" -le 2 ]; then
      # Reviewer pass — emit a non-LGTM comment so the loop enters the fix phase.
      RETRY_OUTPUT="please fix: stubbed reviewer comment $n"
      return 0
    fi
    # Fix phase — simulate exhausted rate limit.
    RETRY_OUTPUT="anthropic: error 429 rate_limit_exceeded (fix stub)"
    return 1
  }

  local rc=0
  run_review_loop "$PLAN_FILE" "$PLAN_NAME" "$PLAN_WORK_DIR" || rc=$?

  assert_eq 1 "$rc" "run_review_loop returns non-zero on fix abort" || return 1
  assert_file_exists "$PLAN_WORK_DIR/pending_status" "pending_status persisted on fix abort" || return 1
  assert_file_exists "$PLAN_FILE" "plan file NOT deleted on fix abort" || return 1
  assert_grep "^reason=fix LLM failed" "$PLAN_WORK_DIR/pending_status" "pending_status records fix abort reason" || return 1
  assert_grep "^failed_iteration=1$" "$PLAN_WORK_DIR/pending_status" "fix abort records iteration 1" || return 1
  assert_grep "switch_all_to_main called" "$SWITCH_CALLS_LOG" "switch_all_to_main invoked on fix abort" || return 1
  assert_grep "fix tail:.*rate_limit_exceeded" "$LOG_FILE" "fix abort log includes output tail" || return 1

  # Restore the simpler stub for any follow-up cases.
  run_llm() {
    RETRY_OUTPUT="anthropic: error 429 rate_limit_exceeded (stub)"
    return 1
  }
  return 0
}

echo "test_abort_preserves_pending:"
run_case "reviewer abort preserves pending state" case_reviewer_abort_preserves_pending
run_case "fix-phase abort preserves pending state" case_fix_abort_preserves_pending

if [ "$failures" -ne 0 ]; then
  echo "test_abort_preserves_pending: $failures FAILED"
  exit 1
fi
echo "test_abort_preserves_pending: all pass"
exit 0
