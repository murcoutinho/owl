#!/bin/bash
# Tests for cross-repo plan dependencies (`depends-on:` frontmatter).
#
# Covers the parser, plan-file lookup, branch derivation from done-file
# names, the resolver's defer/error/ready return codes, the wrong-repo
# isolation rule, and end-to-end injection of the dependency context
# block into the reviewer prompt the review loop builds.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TMPDIR=$(mktemp -d -t owl-deps.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

# --- Build a fake project with two real repos that both have a bare origin.
# Tests need to push real branches to origin so `git fetch` + rev-parse
# resolution behaves the same as it does in production.
FAKE_PROJECT_DIR="$TMPDIR/project"
mkdir -p "$FAKE_PROJECT_DIR"
export FAKE_PROJECT_DIR

create_repo_with_origin() {
  local repo_name="$1"
  local bare="$TMPDIR/bare-$repo_name.git"
  git init -q --bare -b main "$bare"

  local seed="$TMPDIR/seed-$repo_name"
  git init -q -b main "$seed"
  git -C "$seed" -c user.email=seed@test -c user.name=seed commit -q --allow-empty -m "init"
  git -C "$seed" remote add origin "$bare"
  git -C "$seed" push -q origin main
  rm -rf "$seed"

  git clone -q "$bare" "$FAKE_PROJECT_DIR/$repo_name"
  git -C "$FAKE_PROJECT_DIR/$repo_name" config user.email owl@test
  git -C "$FAKE_PROJECT_DIR/$repo_name" config user.name owl

  printf '%s' "$bare"
}

REPO_A_BARE=$(create_repo_with_origin repo-a)
REPO_B_BARE=$(create_repo_with_origin repo-b)
export OWL_TARGET_REPOS="repo-a repo-b"

# Push a feature branch onto a repo's origin from a temp clone, simulating
# a queued-plan branch landing on origin.
push_feature_branch() {
  local repo_name="$1"
  local bare="$2"
  local branch="$3"
  local commit_msg="$4"
  local sandbox="$TMPDIR/push-$repo_name-${branch//\//_}"
  rm -rf "$sandbox"
  git clone -q "$bare" "$sandbox"
  git -C "$sandbox" config user.email owl@test
  git -C "$sandbox" config user.name owl
  git -C "$sandbox" checkout -q -b "$branch"
  echo "feature content for $branch" > "$sandbox/feature.txt"
  git -C "$sandbox" add feature.txt
  git -C "$sandbox" commit -q -m "$commit_msg"
  git -C "$sandbox" push -q origin "$branch"
  rm -rf "$sandbox"
}

# Reviewer providers stay "none" so source_owl skips the env-local merge.
export OWL_IMPL_PROVIDER=none
export OWL_FIX_PROVIDER=none
export OWL_REVIEWER1_PROVIDER=none
export OWL_REVIEWER2_PROVIDER=none

source_owl

LOG_FILE="$TMPDIR/agent.log"
PLAN_DIR="$TMPDIR/plan"
WORK_DIR="$TMPDIR/.work"
mkdir -p "$PLAN_DIR" "$PLAN_DIR/done" "$WORK_DIR"

# After source_owl rebinds PROJECT_DIR to FAKE_PROJECT_DIR, the worktrees
# the resolver inspects are the cloned repos under FAKE_PROJECT_DIR/<name>.
# We never call ensure_plan_workspace in these tests, so EXECUTION_PROJECT_DIR
# stays equal to PROJECT_DIR — exactly the path the per-test fetches go to.

# Seed the queue with the dependency plan files used across tests.
# Plan 100 lives in plan/ — used to test the in-flight + defer paths.
cat > "$PLAN_DIR/100-base-feature.md" <<'EOF'
---
priority: low
---
# Base feature plan

No plan-number references in code.
EOF

# Plan 099 lives in plan/done/ — used to test the done-file path and the
# branch derivation that strips the timestamp suffix.
cat > "$PLAN_DIR/done/099-old-feature_20260101_120000.done.md" <<'EOF'
# Old feature (done)
EOF

# ---- Test runner -----------------------------------------------------------

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

# ---- Parser ---------------------------------------------------------------

case_parser_no_deps() {
  local plan_file="$TMPDIR/no-deps.md"
  cat > "$plan_file" <<'EOF'
---
priority: low
---
# Plan
EOF
  local out
  out=$(parse_plan_dependencies "$plan_file")
  assert_eq "" "$out" "no deps → empty output" || return 1
  return 0
}

case_parser_single_dep() {
  local plan_file="$TMPDIR/single-dep.md"
  cat > "$plan_file" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
---
# Plan
EOF
  local out expected
  out=$(parse_plan_dependencies "$plan_file")
  expected=$(printf 'repo-a\t100')
  assert_eq "$expected" "$out" "single dep parsed" || return 1
  return 0
}

case_parser_multiple_deps() {
  local plan_file="$TMPDIR/multi-deps.md"
  cat > "$plan_file" <<'EOF'
---
priority: low
depends-on:
  - repo: repo-a
    plan: 100
  - repo: repo-b
    plan: 099
review-rounds: 2
---
# Plan
EOF
  local out expected
  out=$(parse_plan_dependencies "$plan_file")
  expected=$(printf 'repo-a\t100\nrepo-b\t099')
  assert_eq "$expected" "$out" "multiple deps parsed in order; later top-level keys end the block" || return 1
  return 0
}

case_parser_quoted_values() {
  local plan_file="$TMPDIR/quoted.md"
  cat > "$plan_file" <<'EOF'
---
depends-on:
  - repo: "repo-a"
    plan: '199'
---
# Plan
EOF
  local out expected
  out=$(parse_plan_dependencies "$plan_file")
  expected=$(printf 'repo-a\t199')
  assert_eq "$expected" "$out" "quoted values stripped" || return 1
  return 0
}

# ---- Plan-file resolver ---------------------------------------------------

case_resolve_plan_file_in_plan_dir() {
  local out
  out=$(resolve_dependency_plan_file 100)
  assert_eq "$PLAN_DIR/100-base-feature.md" "$out" "100 → plan/" || return 1
  return 0
}

case_resolve_plan_file_in_done_dir() {
  local out
  out=$(resolve_dependency_plan_file 099)
  assert_eq "$PLAN_DIR/done/099-old-feature_20260101_120000.done.md" "$out" "099 → plan/done/" || return 1
  return 0
}

case_resolve_plan_file_zero_padded_match() {
  # `plan: 100` should still match a file named 100-… (also: 099 vs 99
  # would match either way through 10#$num normalization).
  local out
  out=$(resolve_dependency_plan_file 99)
  assert_eq "$PLAN_DIR/done/099-old-feature_20260101_120000.done.md" "$out" "99 matches 099-…" || return 1
  return 0
}

case_resolve_plan_file_missing() {
  local rc=0
  resolve_dependency_plan_file 999 >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL: expected non-zero rc for missing plan, got 0" >&2
    return 1
  fi
  return 0
}

# ---- Branch derivation ----------------------------------------------------

case_derive_branch_from_plan_file() {
  local out
  out=$(derive_dependency_branch "$PLAN_DIR/100-base-feature.md")
  assert_eq "owl/100-base-feature" "$out" "live plan → owl/<slug>" || return 1
  return 0
}

case_derive_branch_strips_done_suffix() {
  local out
  out=$(derive_dependency_branch "$PLAN_DIR/done/099-old-feature_20260101_120000.done.md")
  assert_eq "owl/099-old-feature" "$out" "done plan → owl/<slug-without-timestamp>" || return 1
  return 0
}

# ---- Resolver behaviour ---------------------------------------------------

case_resolver_no_deps_returns_zero() {
  local plan="$TMPDIR/no-deps2.md"
  cat > "$plan" <<'EOF'
---
priority: low
---
# Plan
EOF
  local ctx="$TMPDIR/ctx-no-deps.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 0 "$rc" "no deps → rc=0" || return 1
  if [ -s "$ctx" ]; then
    echo "  FAIL: ctx file should be empty when no deps declared" >&2
    return 1
  fi
  return 0
}

case_resolver_unknown_repo_errors() {
  local plan="$TMPDIR/bad-repo.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: not-a-target-repo
    plan: 100
---
EOF
  local ctx="$TMPDIR/ctx-bad-repo.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 2 "$rc" "unknown repo → rc=2 (error)" || return 1
  assert_grep "not in OWL_TARGET_REPOS" "$ctx" "ctx file records repo error" || return 1
  return 0
}

case_resolver_missing_plan_errors() {
  local plan="$TMPDIR/bad-plan.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 999
---
EOF
  local ctx="$TMPDIR/ctx-bad-plan.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 2 "$rc" "missing plan file → rc=2" || return 1
  assert_grep "no plan file with prefix 999" "$ctx" "ctx file explains miss" || return 1
  return 0
}

case_resolver_in_flight_no_branch_defers() {
  # Plan 100 is in plan/ but no branch has been pushed to repo-a's origin.
  local plan="$TMPDIR/in-flight.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
---
EOF
  local ctx="$TMPDIR/ctx-defer.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 1 "$rc" "queued plan + no branch → rc=1 (defer)" || return 1
  assert_grep "DEFER: dependency plan 100" "$LOG_FILE" "log explains the defer reason" || return 1
  return 0
}

case_resolver_branch_in_wrong_repo_is_ignored() {
  # Push the same derived branch name to repo-b only. The dep declares
  # repo-a, so it must defer regardless of repo-b having the branch.
  push_feature_branch repo-b "$REPO_B_BARE" "owl/100-base-feature" "wrong-repo branch"

  : > "$LOG_FILE"
  local plan="$TMPDIR/wrong-repo.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
---
EOF
  local ctx="$TMPDIR/ctx-wrong-repo.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 1 "$rc" "branch only in undeclared repo → still defer" || return 1
  return 0
}

case_resolver_branch_available_emits_context() {
  # Now push the branch to the *correct* repo and confirm we get rc=0
  # plus a populated context file naming the dependency.
  push_feature_branch repo-a "$REPO_A_BARE" "owl/100-base-feature" "real ancestor commit"

  local plan="$TMPDIR/has-branch.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
---
EOF
  local ctx="$TMPDIR/ctx-has-branch.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 0 "$rc" "branch available in declared repo → rc=0" || return 1
  assert_grep "Cross-repo dependency context" "$ctx" "ctx has heading" || return 1
  assert_grep "repo=repo-a plan=100" "$ctx" "ctx names the dependency" || return 1
  assert_grep "owl/100-base-feature" "$ctx" "ctx names the derived branch" || return 1
  assert_grep "feature.txt" "$ctx" "ctx lists the dependency's changed file" || return 1
  return 0
}

case_resolver_done_dependency_uses_done_file_context() {
  # Plan 099 is in plan/done/, so its branch is presumed merged & deleted
  # on origin. Resolver should fall through to the done-file context path
  # instead of deferring.
  local plan="$TMPDIR/done-dep.md"
  cat > "$plan" <<'EOF'
---
depends-on:
  - repo: repo-b
    plan: 099
---
EOF
  local ctx="$TMPDIR/ctx-done.txt"
  local rc=0
  resolve_plan_dependencies "$plan" "$TMPDIR" "$ctx" || rc=$?
  assert_eq 0 "$rc" "done dep with no branch → rc=0 via done-file context" || return 1
  assert_grep "Status: completed" "$ctx" "ctx flags completed dep" || return 1
  assert_grep "Done-file content" "$ctx" "ctx surfaces done-file body" || return 1
  assert_grep "Old feature (done)" "$ctx" "ctx includes done-file body" || return 1
  return 0
}

# ---- Reviewer prompt injection -------------------------------------------

case_review_prompt_includes_dependency_context() {
  # Build a fake plan_work_dir + manifest, drop a dependency_context.txt
  # in it, and run run_review_loop with stubbed run_llm so we can capture
  # the reviewer prompt file the loop wrote.
  local plan_name="201-consumer"
  local plan_file="$PLAN_DIR/$plan_name.md"
  cat > "$plan_file" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
---
# Consumer plan body
EOF

  local pwd_dir="$WORK_DIR/$plan_name"
  rm -rf "$pwd_dir"
  mkdir -p "$pwd_dir"

  # ensure_plan_workspace creates worktrees from the source repos. Use a
  # real repo in the worktree so review_input_$i.tsv can record a real HEAD.
  ensure_plan_workspace "$plan_name" "$pwd_dir"
  local worktree_a="$WORK_DIR/worktrees/$plan_name/repo-a"

  echo "owl/$plan_name" > "$pwd_dir/branch"
  local head_a
  head_a=$(git -C "$worktree_a" rev-parse HEAD)
  printf 'repo-a\t%s\tNONE\t%s\n' "$worktree_a" "$head_a" > "$pwd_dir/review_input_1.tsv"

  # Pre-populate the dependency context the way execute_plan would have.
  cat > "$pwd_dir/dependency_context.txt" <<'EOF'
## Cross-repo dependency context

### Dependency: repo=repo-a plan=100

- Plan file: 100-base-feature.md
- Derived branch: owl/100-base-feature
- Status: branch available on origin (in flight)

#### Changed files (bounded to 50)

```
feature.txt
```
EOF

  # Stub run_llm so the loop short-circuits without touching real LLMs.
  # We just need it to come back successfully; review_input_1.tsv is one
  # row so a single iteration is enough.
  run_llm() {
    RETRY_OUTPUT="LGTM"
    return 0
  }

  # Avoid cleanup_plan_workspace yanking the worktree mid-test.
  cleanup_plan_workspace() { :; }
  switch_all_to_main() { :; }
  push_and_open_prs() { return 0; }
  normalize_all_plan_repos() { return 0; }
  run_deterministic_tests() { return 0; }

  local review_iterations=1
  echo "$review_iterations" > "$pwd_dir/review_iterations"

  local rc=0
  run_review_loop "$plan_file" "$plan_name.md" "$pwd_dir" || rc=$?

  # Iteration 1 reviewer prompt should now include the dep context heading
  # and dependency identifiers. The exact filename matches the format
  # the loop writes.
  local review_prompt="$pwd_dir/review_prompt_1.txt"
  assert_file_exists "$review_prompt" "review prompt was written" || return 1
  assert_grep "Cross-repo dependency context" "$review_prompt" "review prompt includes dep heading" || return 1
  assert_grep "repo=repo-a plan=100" "$review_prompt" "review prompt names the dependency" || return 1
  assert_grep "owl/100-base-feature" "$review_prompt" "review prompt names the derived branch" || return 1

  return 0
}

# ---- Validation ----------------------------------------------------------

case_validate_reports_dependencies() {
  local plan_file="$TMPDIR/validate-dep.md"
  cat > "$plan_file" <<'EOF'
---
depends-on:
  - repo: repo-a
    plan: 100
  - repo: not-a-target-repo
    plan: 100
---
# Plan

No plan-number references in code.
EOF

  VALIDATE_PLAN_FILE="$plan_file"
  local out rc=0
  out=$(validate_plan 2>&1) || rc=$?
  VALIDATE_PLAN_FILE=""

  assert_eq 0 "$rc" "validate exit 0 even with declared error (validation reports it)" || return 1
  if ! printf '%s\n' "$out" | grep -q "Cross-repo dependencies:"; then
    echo "  FAIL: validate output missing 'Cross-repo dependencies:' section" >&2
    printf '%s\n' "$out" >&2
    return 1
  fi
  if ! printf '%s\n' "$out" | grep -q "repo: repo-a, plan: 100"; then
    echo "  FAIL: validate output missing first dep row" >&2
    return 1
  fi
  if ! printf '%s\n' "$out" | grep -q "derived branch: owl/100-base-feature"; then
    echo "  FAIL: validate output missing derived branch" >&2
    return 1
  fi
  if ! printf '%s\n' "$out" | grep -q "ERROR: repo 'not-a-target-repo' not in OWL_TARGET_REPOS"; then
    echo "  FAIL: validate output missing repo-error row" >&2
    return 1
  fi
  return 0
}

echo "test_cross_repo_plan_dependencies:"
run_case "parser: no deps → empty output" case_parser_no_deps
run_case "parser: single dep parsed" case_parser_single_dep
run_case "parser: multiple deps + later top-level keys" case_parser_multiple_deps
run_case "parser: quoted values stripped" case_parser_quoted_values
run_case "resolver: plan in plan/" case_resolve_plan_file_in_plan_dir
run_case "resolver: plan in plan/done/" case_resolve_plan_file_in_done_dir
run_case "resolver: 99 matches 099-…" case_resolve_plan_file_zero_padded_match
run_case "resolver: missing plan returns non-zero" case_resolve_plan_file_missing
run_case "branch: live plan → owl/<slug>" case_derive_branch_from_plan_file
run_case "branch: done plan → strips timestamp" case_derive_branch_strips_done_suffix
run_case "resolve: no deps → rc=0, empty ctx" case_resolver_no_deps_returns_zero
run_case "resolve: unknown repo → rc=2" case_resolver_unknown_repo_errors
run_case "resolve: missing plan file → rc=2" case_resolver_missing_plan_errors
run_case "resolve: queued + no branch → defer" case_resolver_in_flight_no_branch_defers
run_case "resolve: branch in wrong repo is ignored" case_resolver_branch_in_wrong_repo_is_ignored
run_case "resolve: branch in declared repo → ctx populated" case_resolver_branch_available_emits_context
run_case "resolve: done dep → done-file context" case_resolver_done_dependency_uses_done_file_context
run_case "review prompt includes dependency context" case_review_prompt_includes_dependency_context
run_case "validate reports parsed deps + errors" case_validate_reports_dependencies

if [ "$failures" -ne 0 ]; then
  echo "test_cross_repo_plan_dependencies: $failures FAILED"
  exit 1
fi
echo "test_cross_repo_plan_dependencies: all pass"
exit 0
