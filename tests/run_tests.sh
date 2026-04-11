#!/bin/bash
# Minimal runner for owl's bash test suite.
#
# Runs every `tests/test_*.sh` file in a fresh subshell, counts failures,
# and exits non-zero if any test file failed. Each test file is responsible
# for its own setup/teardown (mktemp + trap).
#
# Usage:
#   bash tests/run_tests.sh           # run all tests
#   bash tests/run_tests.sh -v        # show per-test stdout even on success
#
# Individual tests can be run directly too:
#   bash tests/test_run_deterministic_tests.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE=false
if [ "${1:-}" = "-v" ] || [ "${1:-}" = "--verbose" ]; then
  VERBOSE=true
fi

passed=0
failed=0
failing_files=()

shopt -s nullglob
test_files=("$SCRIPT_DIR"/test_*.sh)
shopt -u nullglob

if [ ${#test_files[@]} -eq 0 ]; then
  echo "No test files found in $SCRIPT_DIR" >&2
  exit 1
fi

echo "Running ${#test_files[@]} test file(s) from $SCRIPT_DIR"
echo "========================================="

for test_file in "${test_files[@]}"; do
  name=$(basename "$test_file")
  if $VERBOSE; then
    if bash "$test_file"; then
      passed=$((passed + 1))
    else
      failed=$((failed + 1))
      failing_files+=("$name")
    fi
  else
    output=$(bash "$test_file" 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ]; then
      passed=$((passed + 1))
      echo "✓ $name"
    else
      failed=$((failed + 1))
      failing_files+=("$name")
      echo "✗ $name"
      echo "--- output ---"
      echo "$output"
      echo "--------------"
    fi
  fi
done

echo "========================================="
echo "Summary: $passed passed, $failed failed"
if [ "$failed" -ne 0 ]; then
  echo "Failing files:"
  for f in "${failing_files[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
exit 0
