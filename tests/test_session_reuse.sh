#!/bin/bash
# Integration test for Claude session reuse: --session-id to create,
# --resume to append. This test calls the real Claude CLI (no mocks)
# so it requires a working `claude` binary and valid auth.
#
# Tests the exact pattern owl.sh uses:
# 1. Execution phase: `claude --print --session-id UUID` in PROJECT_DIR
# 2. Fix phase: `claude --resume UUID --print` in the SAME directory
#
# Key finding from manual testing:
# - Sessions are DIRECTORY-SCOPED. A session created in /tmp cannot be
#   resumed from /home. Both calls must run from the same cwd.
# - `--session-id UUID` on a second call fails with "already in use".
#   Must use `--resume UUID` instead.
set -uo pipefail

if ! command -v claude &>/dev/null; then
  echo "SKIP: claude CLI not found in PATH"
  exit 0
fi

TMPDIR=$(mktemp -d -t owl-session.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

# Use the real PROJECT_DIR so sessions are stored in a real project context.
# Claude sessions are scoped to the working directory.
TEST_DIR="$TMPDIR/project"
mkdir -p "$TEST_DIR"
git -C "$TEST_DIR" init -q -b main
git -C "$TEST_DIR" -c user.email=test@test -c user.name=test commit -q --allow-empty -m "init"

SESSION_UUID=$(python3 -c 'import uuid; print(uuid.uuid4())')

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

# --- Case 1: create session with --session-id, resume with --resume ---
case_create_then_resume() {
  local create_out resume_out

  create_out=$(echo "Remember this secret word: watermelon" | \
    (cd "$TEST_DIR" && claude --print --dangerously-skip-permissions --session-id "$SESSION_UUID" -) 2>&1)
  local create_rc=$?
  if [ "$create_rc" -ne 0 ]; then
    echo "  create failed (rc=$create_rc): $create_out" >&2
    return 1
  fi

  resume_out=$(echo "What was the secret word I told you?" | \
    (cd "$TEST_DIR" && claude --print --dangerously-skip-permissions --resume "$SESSION_UUID" -) 2>&1)
  local resume_rc=$?
  if [ "$resume_rc" -ne 0 ]; then
    echo "  resume failed (rc=$resume_rc): $resume_out" >&2
    return 1
  fi

  # The resumed session should remember "watermelon" from the first turn.
  if ! echo "$resume_out" | grep -qi "watermelon"; then
    echo "  resume did not recall the secret word. Output: $resume_out" >&2
    return 1
  fi
  return 0
}

# --- Case 2: --session-id on second call fails ---
case_session_id_reuse_fails() {
  local second_out
  second_out=$(echo "hello again" | \
    (cd "$TEST_DIR" && claude --print --dangerously-skip-permissions --session-id "$SESSION_UUID" -) 2>&1)
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  expected failure but got rc=0. Output: $second_out" >&2
    return 1
  fi
  if ! echo "$second_out" | grep -qi "already in use"; then
    echo "  expected 'already in use' error. Output: $second_out" >&2
    return 1
  fi
  return 0
}

# --- Case 3: resume from different directory fails ---
case_cross_dir_resume_fails() {
  local other_dir="$TMPDIR/other-project"
  mkdir -p "$other_dir"
  git -C "$other_dir" init -q -b main
  git -C "$other_dir" -c user.email=test@test -c user.name=test commit -q --allow-empty -m "init"

  local out
  out=$(echo "recall?" | \
    (cd "$other_dir" && claude --print --dangerously-skip-permissions --resume "$SESSION_UUID" -) 2>&1)
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  expected failure from different directory but got rc=0" >&2
    return 1
  fi
  if ! echo "$out" | grep -qi "No conversation found"; then
    echo "  expected 'No conversation found'. Output: $out" >&2
    return 1
  fi
  return 0
}

echo "test_session_reuse (live Claude CLI):"
run_case "create with --session-id, resume with --resume" case_create_then_resume
run_case "--session-id reuse correctly fails" case_session_id_reuse_fails
run_case "cross-directory resume correctly fails" case_cross_dir_resume_fails

if [ "$failures" -ne 0 ]; then
  echo "test_session_reuse: $failures FAILED"
  exit 1
fi
echo "test_session_reuse: all pass"
exit 0
