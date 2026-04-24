#!/usr/bin/env bash
# Regression test for the standalone pre-queue plan linter
# (src/lint_plan.sh). Verifies the two core behaviors:
#   - Absolute source paths or `<project-root>/` placeholders in
#     Sections "What to change" / "Files to modify" fail the lint.
#   - Repo-relative paths in those sections pass.
#   - Absolute paths elsewhere (Section 3 anchors, prose) are ignored.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LINT="$REPO_ROOT/src/lint_plan.sh"

if [ ! -x "$LINT" ]; then
  echo "FAIL: $LINT missing or not executable" >&2
  exit 2
fi

TMPDIR=$(mktemp -d -t owl-lint-plan.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

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

# --- Fixtures ------------------------------------------------------

good_plan="$TMPDIR/001-good.md"
cat > "$good_plan" <<'EOF'
---
priority: low
---

# 001 — repo-relative edit targets

## Context
Should pass the linter.

## Existing files to anchor on
- /Users/someone/project/repo-a/src/foo.py around line 40
  (anchors may use absolute paths — Section 3 is exempt)

## What to change
- Edit `repo-a/src/foo.py` to add a helper.
- Also touch `repo-a/tests/test_foo.py`.

## Files to modify
- repo-a/src/foo.py
- repo-a/tests/test_foo.py
EOF

bad_absolute_plan="$TMPDIR/002-bad-absolute.md"
cat > "$bad_absolute_plan" <<'EOF'
---
priority: low
---

# 002 — absolute edit target

## Context
Should fail the linter.

## What to change
- Edit `/Users/someone/project/repo-a/src/foo.py`.

## Files to modify
- repo-a/src/foo.py
EOF

bad_placeholder_plan="$TMPDIR/003-bad-placeholder.md"
cat > "$bad_placeholder_plan" <<'EOF'
---
priority: low
---

# 003 — <project-root>/ placeholder

## Context
Should fail the linter.

## What to change
- Edit <project-root>/repo-a/src/foo.py.

## Files to modify
- <project-root>/repo-a/src/foo.py
EOF

code_fence_plan="$TMPDIR/004-code-fence.md"
cat > "$code_fence_plan" <<'EOF'
---
priority: low
---

# 004 — absolute path only inside a fenced code block

## Context
Should pass — snippet contents are not edit targets.

## What to change
- Follow this shape in `repo-a/src/foo.py`:

```python
# /Users/someone/project/should/be/ignored.py
def foo():
    pass
```

## Files to modify
- repo-a/src/foo.py
EOF

# --- Assertions ----------------------------------------------------

case_accepts_repo_relative() {
  local out rc=0
  out=$("$LINT" "$good_plan" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "    expected exit 0, got $rc. Output:" >&2
    echo "$out" >&2
    return 1
  fi
  echo "$out" | grep -q "OK (0 edit-target path violations)" || {
    echo "    expected OK message, got:" >&2
    echo "$out" >&2
    return 1
  }
  return 0
}

case_rejects_absolute() {
  local out rc=0
  out=$("$LINT" "$bad_absolute_plan" 2>&1) || rc=$?
  if [ "$rc" -ne 1 ]; then
    echo "    expected exit 1, got $rc. Output:" >&2
    echo "$out" >&2
    return 1
  fi
  echo "$out" | grep -q "Edit-target path violations" || {
    echo "    expected violation message, got:" >&2
    echo "$out" >&2
    return 1
  }
  echo "$out" | grep -q "/Users/someone" || {
    echo "    expected the offending absolute path to be echoed" >&2
    return 1
  }
  return 0
}

case_rejects_placeholder() {
  local out rc=0
  out=$("$LINT" "$bad_placeholder_plan" 2>&1) || rc=$?
  if [ "$rc" -ne 1 ]; then
    echo "    expected exit 1, got $rc. Output:" >&2
    echo "$out" >&2
    return 1
  fi
  echo "$out" | grep -q "<project-root>/" || {
    echo "    expected <project-root>/ to be flagged" >&2
    return 1
  }
  return 0
}

case_ignores_code_fence() {
  local out rc=0
  out=$("$LINT" "$code_fence_plan" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "    expected exit 0 (fenced snippet should be ignored), got $rc. Output:" >&2
    echo "$out" >&2
    return 1
  fi
  return 0
}

case_missing_file_usage_error() {
  local out rc=0
  out=$("$LINT" "$TMPDIR/does-not-exist.md" 2>&1) || rc=$?
  if [ "$rc" -ne 2 ]; then
    echo "    expected exit 2 for missing file, got $rc. Output:" >&2
    echo "$out" >&2
    return 1
  fi
  return 0
}

echo "test_plan_edit_target_paths:"
run_case "accepts repo-relative edit targets" case_accepts_repo_relative
run_case "rejects absolute paths in edit-target sections" case_rejects_absolute
run_case "rejects <project-root>/ placeholder" case_rejects_placeholder
run_case "ignores absolute paths inside fenced code blocks" case_ignores_code_fence
run_case "returns usage error for missing file" case_missing_file_usage_error

if [ "$failures" -ne 0 ]; then
  echo "test_plan_edit_target_paths: $failures FAILED"
  exit 1
fi
echo "test_plan_edit_target_paths: all pass"
exit 0
