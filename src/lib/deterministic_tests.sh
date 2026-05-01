#!/bin/bash
#
# Deterministic (non-LLM) test runner for the review loop.
#
# Sourced by src/owl.sh after `log` and `find_target_repos` are defined.
# Do not source standalone — depends on owl.sh's globals and helpers.

# Run any deterministic (non-LLM) test commands configured for the target repos.
#
# For each repo in TARGET_REPOS, looks up the env var
# `OWL_TEST_CMD_<repo_name>` (hyphens replaced with underscores). If set, runs
# the command in the repo root and captures combined stdout/stderr.
#
# If `OWL_TEST_SETUP_<repo_name>` is also set, it runs first in the same repo
# root (e.g. `npm ci` to populate node_modules in a fresh worktree). A failing
# setup is recorded as a failure and the test command is skipped for that repo.
#
# Writes a summary of failing suites to `$plan_work_dir/tests_$round.txt` — the
# review loop appends that file to the combined review so the fix agent sees
# test failures alongside LLM reviewer feedback.
#
# Return codes:
#   0 — no failures (either everything passed, or no repo had a command set)
#   1 — at least one configured suite failed
run_deterministic_tests() {
  local plan_work_dir="$1"
  local round="$2"
  local summary_file="$plan_work_dir/tests_$round.txt"
  : > "$summary_file"

  local any_configured=false
  local any_failed=false

  while IFS= read -r -d '' repo_root; do
    local repo_name cmd_var setup_var cmd setup_cmd
    repo_name="$(basename "$repo_root")"
    cmd_var="OWL_TEST_CMD_${repo_name//-/_}"
    setup_var="OWL_TEST_SETUP_${repo_name//-/_}"
    cmd="${!cmd_var:-}"
    setup_cmd="${!setup_var:-}"

    if [ -z "$cmd" ]; then
      continue
    fi
    any_configured=true

    local repo_log="$plan_work_dir/tests_${repo_name}_$round.log"
    : > "$repo_log"

    # `eval` is safe here: `$cmd` and `$setup_cmd` come from operator-controlled
    # env vars in .env.local, same trust boundary as every other OWL_ config
    # var. Do NOT expose either to plan content or LLM output.
    if [ -n "$setup_cmd" ]; then
      log "[Tests] $repo_name: setup: $setup_cmd"
      local setup_rc=0
      ( cd "$repo_root" && eval "$setup_cmd" ) >> "$repo_log" 2>&1 || setup_rc=$?
      if [ "$setup_rc" -ne 0 ]; then
        any_failed=true
        log "[Tests] $repo_name: SETUP FAILED (exit=$setup_rc)"
        {
          echo "### $repo_name — test setup FAILED (exit=$setup_rc)"
          echo ""
          echo "Setup command: \`$setup_cmd\`"
          echo ""
          echo '```'
          tail -n 200 "$repo_log"
          echo '```'
          echo ""
        } >> "$summary_file"
        continue
      fi
    fi

    log "[Tests] $repo_name: $cmd"
    local rc=0
    ( cd "$repo_root" && eval "$cmd" ) >> "$repo_log" 2>&1 || rc=$?

    if [ "$rc" -ne 0 ]; then
      any_failed=true
      log "[Tests] $repo_name: FAILED (exit=$rc)"
      {
        echo "### $repo_name — tests FAILED (exit=$rc)"
        echo ""
        echo "Command: \`$cmd\`"
        echo ""
        echo '```'
        tail -n 200 "$repo_log"
        echo '```'
        echo ""
      } >> "$summary_file"
    else
      log "[Tests] $repo_name: passed"
    fi
  done < <(find_target_repos)

  if ! $any_configured; then
    return 0
  fi
  if $any_failed; then
    return 1
  fi
  return 0
}
