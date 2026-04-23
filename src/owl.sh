#!/bin/bash
#
# Owl — autonomous dev agent
#
# Flow per plan:
# 1. Find next plan
# 2. Reset all repos to main & pull
# 3. Execute plan via Claude Code (told to commit but NOT push)
# 4. Create branch in repos that are ahead of main, move commits there
# 5. Mark pending review
# 6. Review loop: reviewer LLMs check, Claude Code fixes (commits, no push)
# 7. Push branch, open PRs
# 8. Switch all repos back to main
# 9. Write done file
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_DIR="$SCRIPT_DIR/../plan"
LOG_FILE="$SCRIPT_DIR/../agent.log"
WORK_DIR="$SCRIPT_DIR/../.work"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXECUTION_PROJECT_DIR="$PROJECT_DIR"
LOCK_FILE="$SCRIPT_DIR/../.agent.lock"

# Optional local machine-specific config. Keep instance details like
# repository names out of the tracked repo. Tests set OWL_SKIP_ENV_LOCAL=1
# before sourcing so their fixture env isn't clobbered by whatever the
# developer happens to have configured for real runs.
if [ "${OWL_SKIP_ENV_LOCAL:-0}" != "1" ] && [ -f "$SCRIPT_DIR/../.env.local" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/../.env.local"
fi
REVIEW_ITERATIONS=2
MAX_REVIEW_ROUNDS=3
RETRY_WAIT=300
MAX_RETRIES=2
# Hard timeout on any single LLM subprocess. Prevents a stuck claude/codex CLI
# from hanging Owl indefinitely while only emitting heartbeats. Override via
# OWL_LLM_TIMEOUT in .env.local if 40 min isn't enough for the real workloads.
LLM_TIMEOUT="${OWL_LLM_TIMEOUT:-2400}"
POLL_INTERVAL_SECONDS="${OWL_POLL_INTERVAL_SECONDS:-600}"

# Providers / models
IMPL_PROVIDER="${OWL_IMPL_PROVIDER:-claude}"            # claude | codex
IMPL_MODEL="${OWL_IMPL_MODEL:-claude-sonnet-4-6}"
FIX_PROVIDER="${OWL_FIX_PROVIDER:-$IMPL_PROVIDER}"      # claude | codex
FIX_MODEL="${OWL_FIX_MODEL:-$IMPL_MODEL}"

REVIEWER1_PROVIDER="${OWL_REVIEWER1_PROVIDER:-claude}"   # claude | codex | none
REVIEWER1_MODEL="${OWL_REVIEWER1_MODEL:-claude-sonnet-4-6}"
REVIEWER1_LABEL="${OWL_REVIEWER1_LABEL:-Claude Code 1}"

REVIEWER2_PROVIDER="${OWL_REVIEWER2_PROVIDER:-claude}"  # claude | codex | none
REVIEWER2_MODEL="${OWL_REVIEWER2_MODEL:-claude-sonnet-4-6}"
REVIEWER2_LABEL="${OWL_REVIEWER2_LABEL:-Claude Code 2}"

# Review mode: "parallel" or "sequential"
REVIEW_MODE="${OWL_REVIEW_MODE:-parallel}"

# Target repos (space-separated directory names under PROJECT_DIR)
# Only these repos will be managed by Owl. Must be set via environment or the
# ignored .env.local file next to this script. The hard-exit if this is unset
# is deferred until after CLI parsing so --help, --doctor, and --validate can
# run without requiring a full config (useful for first-time setup).
TARGET_REPOS="${OWL_TARGET_REPOS:-}"

# Low-priority plans are skipped when this is "1". Default: include everything.
# Controlled by env var OWL_SKIP_LOW_PRIORITY or the CLI flag --skip-low-priority.
# Use case: run during the day with --skip-low-priority to save tokens, then at
# night re-run without the flag to drain the low-priority queue.
SKIP_LOW_PRIORITY="${OWL_SKIP_LOW_PRIORITY:-0}"

# When set to a non-empty path, owl runs validate_plan() and exits without
# acquiring the lock or entering the main loop. Set by --validate <path>.
VALIDATE_PLAN_FILE=""

# When set to 1, owl runs run_doctor() and exits without acquiring the lock
# or entering the main loop. Set by --doctor.
DOCTOR_MODE=0

# Parse CLI args.
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-low-priority)
      SKIP_LOW_PRIORITY=1
      shift
      ;;
    --include-low-priority)
      SKIP_LOW_PRIORITY=0
      shift
      ;;
    --validate)
      if [ -z "${2:-}" ]; then
        echo "error: --validate requires a plan file path" >&2
        exit 2
      fi
      VALIDATE_PLAN_FILE="$2"
      shift 2
      ;;
    --doctor)
      DOCTOR_MODE=1
      shift
      ;;
    -h|--help)
      cat <<'HELPEOF'
Usage: owl.sh [--skip-low-priority] [--include-low-priority]
              [--validate <plan>] [--doctor]

  --skip-low-priority     Skip plans whose frontmatter has `priority: low`.
                          Also honored via env var OWL_SKIP_LOW_PRIORITY=1.
  --include-low-priority  Force-include low-priority plans even if the env
                          var is set (useful for a nightly drain run).
  --validate <plan>       Parse <plan> and print what the agent would do
                          without calling any LLM or touching git. Exits 0
                          on success; does not acquire the lock file.
  --doctor                Check that required CLIs, credentials, and target
                          repos are in place. Does not call any LLM or touch
                          git. Run this first on a new machine.
  -h, --help              Show this help.

All other configuration is via environment variables -- see README.
HELPEOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Use --help for usage." >&2
      exit 2
      ;;
  esac
done

# Now enforce OWL_TARGET_REPOS for the real-run path. --validate and --doctor
# both need to work without a configured queue so first-time users can debug
# their setup; everything else requires it.
if [ -z "$TARGET_REPOS" ] && [ -z "$VALIDATE_PLAN_FILE" ] && [ "$DOCTOR_MODE" != "1" ]; then
  echo "OWL_TARGET_REPOS is not set. Configure it in the environment or in .env.local." >&2
  echo "Run './src/owl.sh --doctor' to diagnose your setup." >&2
  exit 2
fi

# List repo roots for target repos only (null-delimited).
find_repos_in_project_dir() {
  local project_dir="$1"
  for repo_name in $TARGET_REPOS; do
    local repo_root="$project_dir/$repo_name"
    if git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      printf '%s\0' "$repo_root"
    fi
  done
}

find_source_target_repos() {
  find_repos_in_project_dir "$PROJECT_DIR"
}

find_target_repos() {
  find_repos_in_project_dir "$EXECUTION_PROJECT_DIR"
}

plan_workspace_root() {
  local plan_name="$1"
  printf '%s\n' "$WORK_DIR/worktrees/$plan_name"
}

activate_execution_project_dir() {
  local project_dir="$1"
  EXECUTION_PROJECT_DIR="$project_dir"
}

reset_execution_project_dir() {
  EXECUTION_PROJECT_DIR="$PROJECT_DIR"
}

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Recursively kill a process tree. Used by the LLM timeout watcher to reap
# the full tree (bash -c → node → codex binary, etc.); plain `kill $pid`
# on the wrapper bash doesn't propagate to grandchildren on macOS.
kill_tree() {
  local pid="$1"
  local sig="${2:-TERM}"
  [ -z "$pid" ] && return 0
  local children
  children=$(pgrep -P "$pid" 2>/dev/null || true)
  local c
  for c in $children; do
    kill_tree "$c" "$sig"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}

# ── Proof-of-life ack helpers ──────────────────────────────────────────
#
# run_llm captures the LLM's stdout via `RETRY_OUTPUT=$("$@" 2>&1)`, which
# blocks until completion — so the model's streamed output is invisible
# mid-flight. To get live proof-of-life we instead tell the agent (via
# prompt guidance) to touch a sentinel file on disk as its FIRST tool
# call. A background watcher polls that path and logs a one-shot "alive"
# line to agent.log as soon as the file appears. This bypasses the
# command-substitution buffering entirely because filesystem side effects
# are immediate.

prepend_ack_prompt() {
  # $1: absolute path the agent should write to as its first tool call
  # $2: base prompt file (existing content, unchanged on disk)
  # $3: output path for the wrapped prompt
  local ack_path="$1"
  local base_prompt="$2"
  local out="$3"
  rm -f "$ack_path" 2>/dev/null || true
  {
    echo "PROOF-OF-LIFE REQUIREMENT"
    echo ""
    echo "Your VERY FIRST action MUST be a tool call that writes the single word 'alive' to the absolute path below. Do this BEFORE reading any diff, running any git command, or thinking about the task. The harness cannot see your stdout while you are running — only files on disk — so this is how we confirm you are alive."
    echo ""
    echo "ACK_FILE_PATH: $ack_path"
    echo ""
    echo "After writing the ack file, proceed with the rest of the task below."
    echo ""
    echo "---"
    echo ""
    cat "$base_prompt"
  } > "$out"
}

spawn_ack_watcher() {
  # $1: human-readable label for the log line
  # $2: ack file path to watch
  # $3: (optional) timeout in seconds; default 3600 (1h)
  # Emits the watcher PID on stdout.
  #
  # CRITICAL: this function is called via command substitution
  # (`pid=$(spawn_ack_watcher ...)`). A backgrounded subshell inherits the
  # parent's stdout (the $() pipe) and keeps it open until the subshell
  # exits — which means $() would block for the watcher's entire lifetime,
  # deadlocking the caller before run_llm ever starts. We redirect the
  # subshell's stdout/stderr to /dev/null to detach it from the pipe;
  # log() still writes to $LOG_FILE via `tee -a`, so its output lands in
  # agent.log as intended.
  local label="$1"
  local ack_file="$2"
  local timeout_secs="${3:-3600}"
  (
    local waited=0
    while [ $waited -lt $timeout_secs ]; do
      if [ -f "$ack_file" ]; then
        log "  [$label] agent alive — wrote ack file $ack_file"
        exit 0
      fi
      sleep 2
      waited=$((waited + 2))
    done
  ) >/dev/null 2>&1 &
  echo $!
}

stop_ack_watcher() {
  # Best-effort cleanup. Safe to call with an empty / already-dead PID.
  local pid="$1"
  [ -z "$pid" ] && return 0
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  return 0
}

# Extract just the final-answer block from a reviewer capture.
#
# Codex CLI emits the entire session transcript (tool calls, file reads,
# greps showing inlined code, intermediate reasoning) followed by a
# final block delimited by `^codex$` and `^tokens used$`. The verdict
# is in that final block; everything before it is noise. For a real
# review the noise can be hundreds of kilobytes — last seen 925KB
# combined for one iteration, blowing past Claude's prompt window when
# the fix LLM tried to consume it.
#
# This helper:
#   - locates the LAST `^codex$` line in the file
#   - captures everything up to (but not including) the next
#     `^tokens used$` line, or to EOF if none follows
#   - trims surrounding whitespace
#
# When no `^codex$` marker exists (e.g. a non-codex reviewer whose tool
# just emits the verdict text), it falls back to the trimmed full file
# so existing reviewers keep working.
#
# Both the LGTM-detection gate and the combined-review file passed to
# the fix LLM call this — same compressed text in both places, so the
# gate becomes accurate AND the fix prompt stays small.
extract_reviewer_verdict() {
  local file="$1"
  [ -f "$file" ] || return 0
  if grep -q '^codex$' "$file" 2>/dev/null; then
    awk '
      /^codex$/        { buf = ""; capturing = 1; next }
      /^tokens used$/  { capturing = 0; next }
      capturing        { buf = buf $0 "\n" }
      END              { printf "%s", buf }
    ' "$file" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
  else
    sed 's/^[[:space:]]*//;s/[[:space:]]*$//' "$file"
  fi
}

# Run any deterministic (non-LLM) test commands configured for the target repos.
#
# For each repo in TARGET_REPOS, looks up the env var
# `OWL_TEST_CMD_<repo_name>` (hyphens replaced with underscores). If set, runs
# the command in the repo root and captures combined stdout/stderr.
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
    local repo_name var_name cmd
    repo_name="$(basename "$repo_root")"
    var_name="OWL_TEST_CMD_${repo_name//-/_}"
    cmd="${!var_name:-}"

    if [ -z "$cmd" ]; then
      continue
    fi
    any_configured=true

    local repo_log="$plan_work_dir/tests_${repo_name}_$round.log"
    log "[Tests] $repo_name: $cmd"

    # `eval` is safe here: `$cmd` comes from `OWL_TEST_CMD_<repo>` which is
    # operator-controlled via .env.local, same trust boundary as every other
    # OWL_ config var. Do NOT expose this to plan content or LLM output.
    local rc=0
    ( cd "$repo_root" && eval "$cmd" ) > "$repo_log" 2>&1 || rc=$?

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

format_poll_interval() {
  local seconds="$1"
  if [ "$seconds" -eq 60 ]; then
    echo "1 minute"
  elif [ $((seconds % 60)) -eq 0 ]; then
    local minutes=$((seconds / 60))
    echo "$minutes minutes"
  else
    echo "$seconds seconds"
  fi
}

normalize_provider() {
  echo "${1:-}" | tr '[:upper:]' '[:lower:]'
}

normalize_model_name() {
  echo "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs
}

reviewer_slot_enabled() {
  local provider model
  provider="$(normalize_provider "$1")"
  model="$(normalize_model_name "$2")"

  if [ "$provider" = "none" ] || [ -z "$provider" ]; then
    return 1
  fi
  if [ "$model" = "none" ] || [ -z "$model" ]; then
    return 1
  fi
  return 0
}

repo_has_local_changes() {
  local repo_root="$1"
  if ! git -C "$repo_root" diff --quiet 2>/dev/null || \
     ! git -C "$repo_root" diff --cached --quiet 2>/dev/null || \
     [ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
    return 0
  fi
  return 1
}

ensure_repo_worktree() {
  local source_repo_root="$1"
  local repo_name="$2"
  local worktree_root="$3"

  if [ -e "$worktree_root" ]; then
    if git -C "$worktree_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      return 0
    fi
    log "  $repo_name: ERROR — worktree path exists but is not a git worktree: $worktree_root"
    return 1
  fi

  if ! git -C "$source_repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
    log "  $repo_name: SKIPPING — source repo has no commits, cannot create worktree"
    return 0
  fi

  mkdir -p "$(dirname "$worktree_root")"
  git -C "$source_repo_root" worktree prune >/dev/null 2>>"$LOG_FILE" || true

  local add_output=""
  add_output=$(git -C "$source_repo_root" worktree add --detach "$worktree_root" HEAD 2>&1) || {
    log "  $repo_name: ERROR — failed to create worktree at $worktree_root"
    log "  $add_output"
    return 1
  }
  return 0
}

ensure_plan_workspace() {
  local plan_name="$1"
  local plan_work_dir="$2"
  local workspace_root
  workspace_root="$(plan_workspace_root "$plan_name")"

  mkdir -p "$workspace_root"
  while IFS= read -r -d '' repo_root; do
    local repo_name
    repo_name="$(basename "$repo_root")"
    ensure_repo_worktree "$repo_root" "$repo_name" "$workspace_root/$repo_name" || return 1
  done < <(find_source_target_repos)

  printf '%s\n' "$workspace_root" > "$plan_work_dir/execution_project_dir"
  activate_execution_project_dir "$workspace_root"
  return 0
}

cleanup_plan_workspace() {
  local plan_name="$1"
  local plan_work_dir="${2:-}"
  local workspace_root
  workspace_root="$(plan_workspace_root "$plan_name")"

  while IFS= read -r -d '' repo_root; do
    local repo_name worktree_root
    repo_name="$(basename "$repo_root")"
    worktree_root="$workspace_root/$repo_name"
    if [ -e "$worktree_root" ]; then
      git -C "$repo_root" worktree remove --force "$worktree_root" >/dev/null 2>>"$LOG_FILE" || \
        log "  $repo_name: WARNING — failed to remove worktree $worktree_root"
    fi
    git -C "$repo_root" worktree prune >/dev/null 2>>"$LOG_FILE" || true
  done < <(find_source_target_repos)

  rm -rf "$workspace_root"
  if [ -n "$plan_work_dir" ]; then
    rm -f "$plan_work_dir/execution_project_dir"
  fi
  if [ "$EXECUTION_PROJECT_DIR" = "$workspace_root" ]; then
    reset_execution_project_dir
  fi
}

ensure_plan_branch_checked_out() {
  local repo_root="$1"
  local repo_name="$2"
  local branch_name="$3"

  local current_branch=""
  current_branch="$(git -C "$repo_root" branch --show-current 2>/dev/null || true)"
  if [ "$current_branch" = "$branch_name" ]; then
    return 0
  fi

  if git -C "$repo_root" checkout "$branch_name" >/dev/null 2>>"$LOG_FILE"; then
    return 0
  fi

  if ! repo_has_local_changes "$repo_root"; then
    log "  $repo_name: ERROR — failed to checkout branch '$branch_name'"
    return 1
  fi

  local stash_name
  stash_name="owl-normalize-${branch_name//\//-}-$(date +%s)"
  log "  $repo_name: stashing local changes to move them onto '$branch_name'"
  local stash_output=""
  stash_output=$(git -C "$repo_root" stash push -u -m "$stash_name" 2>&1) || {
    log "  $repo_name: ERROR — failed to stash local changes before checkout"
    log "  $stash_output"
    return 1
  }

  if ! git -C "$repo_root" checkout "$branch_name" >/dev/null 2>>"$LOG_FILE"; then
    log "  $repo_name: ERROR — failed to checkout branch '$branch_name' even after stashing"
    return 1
  fi

  if git -C "$repo_root" stash list | grep -Fq "$stash_name"; then
    local apply_output=""
    apply_output=$(git -C "$repo_root" stash apply "stash^{/$stash_name}" 2>&1) || {
      log "  $repo_name: ERROR — failed to reapply stashed changes on '$branch_name'"
      log "  $apply_output"
      return 1
    }
    git -C "$repo_root" stash drop "stash^{/$stash_name}" >/dev/null 2>>"$LOG_FILE" || true
  fi

  return 0
}

normalize_repo_changes_for_plan() {
  local repo_root="$1"
  local repo_name="$2"
  local branch_name="$3"
  local commit_message="$4"
  local base_hash="$5"
  local manifest_file="${6:-}"
  local commits_file="${7:-}"
  local branch_mode="${8:-reuse}"

  if ! repo_has_local_changes "$repo_root"; then
    return 0
  fi

  if [ "$branch_mode" = "create" ]; then
    log "  $repo_name: changes detected → creating branch '$branch_name'"
    if git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
      log "  $repo_name: deleting stale branch '$branch_name'"
      if ! git -C "$repo_root" branch -D "$branch_name" 2>&1 | tee -a "$LOG_FILE"; then
        log "  $repo_name: WARNING — could not delete stale branch (maybe checked out?)"
      fi
    fi

    local checkout_err=""
    checkout_err=$(git -C "$repo_root" checkout -b "$branch_name" 2>&1) || {
      log "  $repo_name: ERROR — 'git checkout -b $branch_name' failed:"
      log "  $checkout_err"
      return 1
    }
  else
    ensure_plan_branch_checked_out "$repo_root" "$repo_name" "$branch_name" || return 1
  fi

  if ! repo_has_local_changes "$repo_root"; then
    return 0
  fi

  local before_hash="NONE"
  if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
    before_hash="$(git -C "$repo_root" rev-parse HEAD)"
  fi

  local add_err=""
  add_err=$(git -C "$repo_root" add -A 2>&1) || {
    log "  $repo_name: ERROR — 'git add -A' failed:"
    log "  $add_err"
    return 1
  }

  local commit_output=""
  commit_output=$(git -C "$repo_root" commit -m "$commit_message" 2>&1) || {
    if printf '%s\n' "$commit_output" | grep -qi "nothing to commit"; then
      return 0
    fi
    log "  $repo_name: ERROR — 'git commit' failed. Worktree changes are preserved on branch '$branch_name' for investigation."
    log "  git commit output: $commit_output"
    log "  git status:"
    git -C "$repo_root" status --short 2>&1 | while IFS= read -r line; do log "    $line"; done
    return 1
  }

  local after_hash
  after_hash="$(git -C "$repo_root" rev-parse HEAD)"
  local short_hash
  short_hash="$(git -C "$repo_root" rev-parse --short HEAD)"
  log "  $repo_name: committed ($short_hash)"

  if [ -n "$manifest_file" ] && [ "$after_hash" != "$base_hash" ] && [ "$after_hash" != "$before_hash" ]; then
    printf '%s\t%s\t%s\t%s\n' "$repo_name" "$repo_root" "$base_hash" "$after_hash" >> "$manifest_file"
  fi
  if [ -n "$commits_file" ] && [ "$after_hash" != "$before_hash" ]; then
    printf '%s\t%s\n' "$repo_name" "$short_hash" >> "$commits_file"
  fi
  return 0
}

normalize_all_plan_repos() {
  local branch_name="$1"
  local commit_message="$2"
  local plan_work_dir="$3"
  local manifest_file="$4"
  local branch_mode="${5:-reuse}"

  local had_error=false
  while IFS= read -r -d '' repo_root; do
    local repo_name base_hash recorded_base
    repo_name="$(basename "$repo_root")"

    if [ "$branch_mode" = "reuse" ] && \
       ! git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1 && \
       ! repo_has_local_changes "$repo_root"; then
      continue
    fi

    base_hash="NONE"
    recorded_base=$(awk -F $'\t' -v repo="$repo_name" -v root="$repo_root" \
      '$1 == repo && $2 == root { print $3; exit }' "$plan_work_dir/execution_base.tsv" 2>/dev/null || true)
    if [ -n "$recorded_base" ]; then
      base_hash="$recorded_base"
    elif git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      base_hash="$(git -C "$repo_root" rev-parse HEAD)"
    fi

    if ! normalize_repo_changes_for_plan \
      "$repo_root" \
      "$repo_name" \
      "$branch_name" \
      "$commit_message" \
      "$base_hash" \
      "$manifest_file" \
      "$plan_work_dir/commits.tsv" \
      "$branch_mode"; then
      had_error=true
    fi
  done < <(find_target_repos)

  ! $had_error
}

# Print the plan file with YAML frontmatter (--- ... ---) stripped.
strip_plan_frontmatter() {
  local plan_file="$1"
  awk '
    NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
    in_fm && /^---[[:space:]]*$/ { in_fm = 0; next }
    !in_fm { print }
  ' "$plan_file"
}

# Parse review-rounds from plan frontmatter. Prints the clamped value,
# or $REVIEW_ITERATIONS if missing/invalid/no frontmatter.
parse_plan_review_rounds() {
  local plan_file="$1"
  local value
  value=$(awk '
    NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
    in_fm && /^---[[:space:]]*$/ { exit }
    in_fm && /^[[:space:]]*review[-_]rounds[[:space:]]*:/ {
      sub(/^[[:space:]]*review[-_]rounds[[:space:]]*:[[:space:]]*/, "")
      gsub(/[[:space:]]/, "")
      print
      exit
    }
  ' "$plan_file")

  if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ]; then
    if [ "$value" -gt "$MAX_REVIEW_ROUNDS" ]; then
      echo "$MAX_REVIEW_ROUNDS"
    else
      echo "$value"
    fi
  else
    echo "$REVIEW_ITERATIONS"
  fi
}

# Parse `priority:` from plan frontmatter. Prints "low" if the plan declares
# low priority, empty string otherwise (meaning normal priority).
# Accepted values: "low" (case-insensitive). Anything else is treated as normal.
parse_plan_priority() {
  local plan_file="$1"
  local value
  value=$(awk '
    NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
    in_fm && /^---[[:space:]]*$/ { exit }
    in_fm && /^[[:space:]]*priority[[:space:]]*:/ {
      sub(/^[[:space:]]*priority[[:space:]]*:[[:space:]]*/, "")
      sub(/[[:space:]]+$/, "")
      gsub(/^["'"'"']|["'"'"']$/, "")
      print
      exit
    }
  ' "$plan_file")
  # Normalize to lowercase and only emit "low"; anything else → "".
  value=$(echo "$value" | tr '[:upper:]' '[:lower:]')
  if [ "$value" = "low" ]; then
    echo "low"
  fi
}

# Parse `base-branch:` from plan frontmatter. Prints the literal branch name,
# or an empty string if missing/blank. Lets a plan declare a dependency on
# another plan's branch so it can be executed before that plan is merged.
parse_plan_base_branch() {
  local plan_file="$1"
  awk '
    NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
    in_fm && /^---[[:space:]]*$/ { exit }
    in_fm && /^[[:space:]]*base[-_]branch[[:space:]]*:/ {
      sub(/^[[:space:]]*base[-_]branch[[:space:]]*:[[:space:]]*/, "")
      # trim trailing whitespace first so the closing quote (if any) lands at end-of-string
      sub(/[[:space:]]+$/, "")
      # then strip surrounding quotes if present
      gsub(/^["'"'"']|["'"'"']$/, "")
      print
      exit
    }
  ' "$plan_file"
}

run_llm() {
  local provider
  provider="$(normalize_provider "$1")"
  local model="$2"
  local prompt_file="$3"
  local session_id="${4:-}"
  # "create" uses --session-id (first call, takes exclusive lock).
  # "resume" uses --resume  (subsequent calls, appends to existing session).
  # Sessions are directory-scoped — both calls must run from the same cwd.
  local session_mode="${5:-create}"

  local prompt_name
  prompt_name=$(basename "$prompt_file")
  local session_info=""
  [ -n "$session_id" ] && session_info=" session=${session_id:0:8}($session_mode)"
  local llm_tag="$provider $model [$prompt_name]$session_info"

  local start_ts
  start_ts=$(date +%s)
  log "LLM START: $llm_tag"

  local _llm_rc=0
  case "$provider" in
    claude)
      if [ -n "$session_id" ] && [ "$session_mode" = "resume" ]; then
        # shellcheck disable=SC2016
        retry_on_limit "$llm_tag" bash -c 'claude --print --dangerously-skip-permissions --model "$1" --resume "$2" - < "$3"' _ "$model" "$session_id" "$prompt_file" || _llm_rc=$?
      elif [ -n "$session_id" ]; then
        # shellcheck disable=SC2016
        retry_on_limit "$llm_tag" bash -c 'claude --print --dangerously-skip-permissions --model "$1" --session-id "$2" - < "$3"' _ "$model" "$session_id" "$prompt_file" || _llm_rc=$?
      else
        # shellcheck disable=SC2016
        retry_on_limit "$llm_tag" bash -c 'claude --print --dangerously-skip-permissions --model "$1" - < "$2"' _ "$model" "$prompt_file" || _llm_rc=$?
      fi
      ;;
    codex)
      # shellcheck disable=SC2016
      retry_on_limit "$llm_tag" bash -c 'codex exec --full-auto --skip-git-repo-check --model "$1" - < "$2"' _ "$model" "$prompt_file" || _llm_rc=$?
      ;;
    none)
      RETRY_OUTPUT=""
      log "LLM DONE: $llm_tag — skipped (provider=none)"
      return 0
      ;;
    *)
      log "LLM FAILED: invalid provider '$provider' (expected claude, codex, or none)"
      return 1
      ;;
  esac

  local elapsed=$(( $(date +%s) - start_ts ))
  local min=$((elapsed / 60))
  local sec=$((elapsed % 60))
  if [ $_llm_rc -eq 0 ]; then
    log "LLM DONE: $llm_tag — success, elapsed ${min}m${sec}s, ${#RETRY_OUTPUT} chars"
  else
    local tail_line
    tail_line=$(printf '%s\n' "$RETRY_OUTPUT" | grep -v '^[[:space:]]*$' | tail -n 1 | head -c 300)
    log "LLM FAILED: $llm_tag — exit=$_llm_rc, elapsed ${min}m${sec}s. Last output line: ${tail_line:-<empty>}"
  fi
  return $_llm_rc
}

get_or_create_coder_session_id() {
  local plan_work_dir="$1"
  local session_file="$plan_work_dir/coder_session_id"
  if [ -f "$session_file" ]; then
    head -n 1 "$session_file"
    return 0
  fi
  local sid
  sid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  printf '%s\n' "$sid" > "$session_file"
  echo "$sid"
}

load_coder_session_id() {
  local plan_work_dir="$1"
  local session_file="$plan_work_dir/coder_session_id"
  if [ -f "$session_file" ]; then
    head -n 1 "$session_file"
  fi
}

acquire_lock() {
  if mkdir "$LOCK_FILE" 2>/dev/null; then
    echo $$ > "$LOCK_FILE/pid"
    trap 'rm -rf "$LOCK_FILE"' EXIT INT TERM
  elif kill -0 "$(cat "$LOCK_FILE/pid" 2>/dev/null)" 2>/dev/null; then
    echo "Another agent instance is running (PID $(cat "$LOCK_FILE/pid")). Exiting."
    exit 1
  else
    log "Stale lock found (process dead). Reclaiming."
    rm -rf "$LOCK_FILE"
    acquire_lock
  fi
}

is_rate_limited() {
  local output="$1"
  # NOTE: bare "429", "try again", and "capacity" are too lossy — they match
  # ordinary code-review output (e.g. file references like "models.py:429:").
  # Require HTTP-status context for 429 and rely on the more distinctive phrases.
  echo "$output" | grep -qiE "rate.?limit|too many requests|quota exceeded|overloaded|(HTTP|status|code|error)[/: ]+429\b"
}

rate_limit_excerpt() {
  local output="$1"
  python3 - "$output" <<'PY'
import re
import sys

text = sys.argv[1]
pattern = re.compile(r"rate.?limit|too many requests|quota exceeded|overloaded|(HTTP|status|code|error)[/: ]+429\b", re.I)
for line in text.splitlines():
    if pattern.search(line):
        print(line[:500])
        break
PY
}

retry_on_limit() {
  local desc="$1"
  shift
  local attempt=0
  while true; do
    attempt=$((attempt + 1))
    if [ $attempt -gt 1 ]; then
      log "LLM ATTEMPT: $desc — retry $attempt/$MAX_RETRIES starting"
    fi

    # In-flight heartbeat — emits a "WORKING" line every 5 min so the console
    # never goes silent for longer than that while the LLM is executing.
    # Killed the instant the CLI returns (success or failure). `disown`
    # before kill suppresses bash's "Terminated: 15" job-control notice.
    local heartbeat_pid
    (
      local waited=0
      while sleep 300; do
        waited=$((waited + 300))
        log "LLM WORKING: $desc — still running after $((waited / 60))m..."
      done
    ) 2>/dev/null &
    heartbeat_pid=$!

    # Run the LLM in the background with its stdout+stderr captured to a
    # tempfile (not via $(...) — we need a PID we can kill on timeout).
    # Wrap via perl+POSIX::setsid so the LLM and all its descendants share
    # a fresh process group; killing `-$pgid` then reliably reaps the
    # entire tree in one go, avoiding the kill_tree race where a dying
    # wrapper orphans its grandchildren before pgrep can see them.
    local llm_tmp
    llm_tmp=$(mktemp -t owl_llm_out.XXXXXX)
    perl -e 'use POSIX; POSIX::setsid() or die "setsid: $!"; exec @ARGV' "$@" \
      >"$llm_tmp" 2>&1 &
    local llm_pid=$!
    # In the new session the LLM's PID == its PGID.
    local llm_pgid="$llm_pid"

    # Timeout watcher: kill the whole process group after LLM_TIMEOUT
    # seconds if the LLM hasn't returned. SIGTERM first; SIGKILL 3s later
    # as fallback. kill_tree is a belt-and-braces backup in case the
    # process-group kill ever misses something.
    (
      sleep "$LLM_TIMEOUT"
      if kill -0 "$llm_pid" 2>/dev/null; then
        kill -TERM "-$llm_pgid" 2>/dev/null || true
        kill_tree "$llm_pid" TERM
        sleep 3
        kill -KILL "-$llm_pgid" 2>/dev/null || true
        kill_tree "$llm_pid" KILL
      fi
    ) >/dev/null 2>&1 &
    local timeout_pid=$!

    local rc=0
    wait "$llm_pid" 2>/dev/null || rc=$?

    # Did the timeout fire? A killed-by-signal child returns 128+sig.
    local timed_out=false
    if [ $rc -eq 143 ] || [ $rc -eq 137 ]; then
      timed_out=true
    fi

    # Reap the output and stop the timeout watcher (may already have fired).
    RETRY_OUTPUT=$(cat "$llm_tmp" 2>/dev/null || true)
    rm -f "$llm_tmp"
    disown "$timeout_pid" 2>/dev/null || true
    kill "$timeout_pid" 2>/dev/null || true
    pkill -P "$timeout_pid" 2>/dev/null || true

    # Stop the heartbeat and its sleep child deterministically. pkill -P
    # catches the inner `sleep` in case the subshell kill doesn't propagate.
    disown "$heartbeat_pid" 2>/dev/null || true
    kill "$heartbeat_pid" 2>/dev/null || true
    pkill -P "$heartbeat_pid" 2>/dev/null || true

    # Hard timeout is a terminal failure for this call — don't retry. Rate-
    # limit loops would eat another 2×(25m + 5m) = 60m if we retried, and
    # timeouts usually mean the CLI is wedged, not rate-limited.
    if $timed_out; then
      local tail_line
      tail_line=$(printf '%s\n' "$RETRY_OUTPUT" | grep -v '^[[:space:]]*$' | tail -n 1 | head -c 300)
      log "LLM TIMEOUT: $desc — killed after $((LLM_TIMEOUT / 60))m (exit=$rc). Last output line: ${tail_line:-<empty>}"
      return 124
    fi

    # If the underlying command succeeded, trust it — never second-guess based
    # on output prose. The is_rate_limited grep matches phrases like "rate
    # limit" wherever they appear (including inside echoed user prompts and
    # ordinary review feedback that discusses rate limiting). Without this
    # short-circuit we'd false-positive on every plan or diff that mentions
    # the words "rate limit", and sleep 10 minutes for nothing.
    if [ $rc -eq 0 ]; then
      return 0
    fi
    if is_rate_limited "$RETRY_OUTPUT"; then
      local excerpt
      excerpt="$(rate_limit_excerpt "$RETRY_OUTPUT")"
      if [ $attempt -ge $MAX_RETRIES ]; then
        log "LLM RATE-LIMITED: $desc — gave up after $attempt attempts (exit=$rc). Trigger: ${excerpt:-<no matching line found>}"
        return 1
      fi
      local wait_minutes=$((RETRY_WAIT / 60))
      local next_attempt=$((attempt + 1))
      local resume_epoch=$(( $(date +%s) + RETRY_WAIT ))
      local resume_time
      resume_time=$(date -r "$resume_epoch" '+%H:%M:%S' 2>/dev/null || date -d "@$resume_epoch" '+%H:%M:%S' 2>/dev/null || echo "?")
      log "LLM RATE-LIMITED: $desc — attempt $attempt/$MAX_RETRIES (exit=$rc). Trigger: ${excerpt:-<no matching line found>}"
      log "LLM BACKOFF: waiting $wait_minutes min before attempt $next_attempt/$MAX_RETRIES (resume at $resume_time). Interrupt owl.sh to abort."
      # Backoff heartbeat — 5-min ticks during the sleep so the log keeps
      # breathing at the same cadence as the in-flight heartbeat above.
      local backoff_waited=0
      while [ $backoff_waited -lt $RETRY_WAIT ]; do
        local chunk=300
        local remaining=$((RETRY_WAIT - backoff_waited))
        [ $chunk -gt $remaining ] && chunk=$remaining
        sleep "$chunk"
        backoff_waited=$((backoff_waited + chunk))
        local left=$((RETRY_WAIT - backoff_waited))
        if [ $left -gt 0 ]; then
          log "LLM BACKOFF: still waiting ${left}s until retry at $resume_time"
        fi
      done
      log "LLM BACKOFF: wait finished — starting attempt $next_attempt/$MAX_RETRIES"
    else
      return $rc
    fi
  done
}

# ─── Step 2: Reset all repos to the base branch & pull ───
# If base_branch is empty, resets to main (default). Otherwise, for each repo:
#   - if origin has base_branch, fetch and check it out
#   - if origin does not have base_branch, fall back to main for that repo
#     (the dependency is considered satisfied — most likely the base plan was
#     already merged and its branch deleted)
reset_all_repos_to_base() {
  local base_branch="$1"
  local discard_owned_changes="${2:-0}"
  if [ -z "$base_branch" ]; then
    log "Resetting all execution repos to main..."
  else
    log "Resetting all execution repos to base branch '$base_branch' (fallback: main)..."
  fi

  while IFS= read -r -d '' repo_root; do
    local repo_name
    repo_name="$(basename "$repo_root")"

    # Skip repos with no commits
    if ! git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      continue
    fi

    if repo_has_local_changes "$repo_root"; then
      if [ "$discard_owned_changes" = "1" ]; then
        log "  $repo_name: discarding stale Owl-managed worktree changes before reset"
        git -C "$repo_root" reset --hard HEAD >/dev/null 2>>"$LOG_FILE" || {
          log "  $repo_name: WARNING — failed to reset worktree"
          continue
        }
        git -C "$repo_root" clean -fd >/dev/null 2>>"$LOG_FILE" || {
          log "  $repo_name: WARNING — failed to clean worktree"
          continue
        }
      else
        log "  $repo_name: SKIPPING — has local changes"
        continue
      fi
    fi

    # Decide which branch this repo should land on. Start from the requested
    # base; if origin does not have it, fall back to main for this repo.
    #
    # We REQUIRE the explicit fetch to succeed, not just the cached ref to
    # exist. Otherwise a stale `refs/remotes/origin/<base>` cached from an
    # earlier Owl run (when the base plan was still in flight) keeps fooling
    # rev-parse into thinking the branch is still on origin, even after the
    # base plan was merged and its upstream branch auto-deleted. That stale-
    # ref trap would make the dependent plan run on yesterday's base instead
    # of today's main.
    local target_branch="main"
    local target_ref="main"
    if [ -n "$base_branch" ]; then
      if git -C "$repo_root" fetch origin "$base_branch" 2>/dev/null && \
         git -C "$repo_root" rev-parse --verify "refs/remotes/origin/$base_branch" >/dev/null 2>&1; then
        target_branch="$base_branch"
        target_ref="refs/remotes/origin/$base_branch"
        log "  $repo_name: using base branch '$base_branch' from origin"
      else
        log "  $repo_name: base branch '$base_branch' not on origin — falling back to main"
        # Prune the stale cached ref if one exists, so any later command on
        # this repo does not accidentally resurrect it.
        git -C "$repo_root" update-ref -d "refs/remotes/origin/$base_branch" 2>/dev/null || true
      fi
    elif git -C "$repo_root" fetch origin main 2>/dev/null && \
         git -C "$repo_root" rev-parse --verify "refs/remotes/origin/main" >/dev/null 2>&1; then
      target_ref="refs/remotes/origin/main"
    fi

    log "  $repo_name: detaching worktree at '$target_branch'"
    git -C "$repo_root" checkout --detach "$target_ref" >/dev/null 2>>"$LOG_FILE" || {
      log "  $repo_name: WARNING — failed to detach worktree at '$target_ref'"
      continue
    }

  done < <(find_target_repos)
}


# ─── Step 7: Push branches and open PRs ───
push_and_open_prs() {
  local branch_name="$1"
  local plan_name="$2"
  local plan_file="$3"
  local plan_work_dir="$4"
  local review_rounds_completed="$5"
  local review_rounds_total="$6"

  local plan_content
  plan_content=$(strip_plan_frontmatter "$plan_file")

  # Recover the plan's base branch (if any). Persisted by execute_plan so
  # resume paths pick it up without re-parsing the plan file.
  local pr_base_branch=""
  if [ -f "$plan_work_dir/base_branch" ]; then
    pr_base_branch="$(cat "$plan_work_dir/base_branch" 2>/dev/null)"
  fi

  local had_pr_failure=false

  while IFS= read -r -d '' repo_root; do
    local repo_name
    repo_name="$(basename "$repo_root")"

    # Skip repos that don't have this branch
    if ! git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
      continue
    fi

    # Determine the PR base for this repo. Default to main; if the plan
    # declared a base branch AND origin CURRENTLY has it, target that
    # instead. Same stale-ref defense as reset_all_repos_to_base: require
    # the explicit fetch to succeed, not just the cached ref to exist.
    # Otherwise `gh pr create --base <deleted-branch>` would fail after the
    # ancestor plan merged and its branch was auto-deleted.
    local repo_pr_base="main"
    if [ -n "$pr_base_branch" ]; then
      if git -C "$repo_root" fetch origin "$pr_base_branch" 2>/dev/null && \
         git -C "$repo_root" rev-parse --verify "refs/remotes/origin/$pr_base_branch" >/dev/null 2>&1; then
        repo_pr_base="$pr_base_branch"
      else
        git -C "$repo_root" update-ref -d "refs/remotes/origin/$pr_base_branch" 2>/dev/null || true
      fi
    fi

    # Skip if no commits on branch beyond the PR base
    if [ -z "$(git -C "$repo_root" log "origin/${repo_pr_base}..${branch_name}" --oneline 2>/dev/null)" ]; then
      git -C "$repo_root" branch -d "$branch_name" 2>/dev/null || true
      continue
    fi

    log "Pushing branch '$branch_name' in $repo_name (PR base: $repo_pr_base)..."
    if ! git -C "$repo_root" checkout "$branch_name" 2>>"$LOG_FILE"; then
      log "  $repo_name: failed to checkout branch. PR creation failed."
      had_pr_failure=true
      continue
    fi
    if ! git -C "$repo_root" push -u origin "$branch_name" 2>>"$LOG_FILE"; then
      log "  $repo_name: push failed. PR creation failed."
      had_pr_failure=true
      continue
    fi

    log "Opening PR in $repo_name..."
    local pr_url
    pr_url=$(cd "$repo_root" && gh pr create \
      --base "$repo_pr_base" \
      --title "[owl] ${plan_name%.md}" \
      --body "$(cat <<EOF
## ${plan_name%.md}

**Review rounds completed:** ${review_rounds_completed} / ${review_rounds_total}

## Plan

\`\`\`
${plan_content}
\`\`\`

---
Generated by [Owl](${OWL_REPO_URL:-https://github.com/murcoutinho/owl})
EOF
)" 2>&1) || true

    if echo "$pr_url" | grep -q "^https://"; then
      log "PR created in $repo_name: $pr_url"
      echo "$repo_name	$pr_url" >> "$plan_work_dir/pull_requests.tsv"
    else
      log "Failed to create PR in $repo_name: $pr_url"
      had_pr_failure=true
    fi

  done < <(find_target_repos)

  ! $had_pr_failure
}

# ─── Step 8: Switch all repos back to main ───
switch_all_to_main() {
  while IFS= read -r -d '' repo_root; do
    if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      git -C "$repo_root" checkout --detach main >/dev/null 2>>"$LOG_FILE" || \
        log "  $(basename "$repo_root"): WARNING — failed to detach at main"
    fi
  done < <(find_target_repos)
}

# ─── Execute plan ───
execute_plan() {
  local plan_file="$1"
  local plan_name
  plan_name="$(basename "$plan_file")"
  local plan_slug="${plan_name%.md}"

  log "========================================="
  log "Found plan: $plan_name"
  log "========================================="

  local plan_content
  plan_content="$(strip_plan_frontmatter "$plan_file")"

  local plan_review_iterations
  plan_review_iterations="$(parse_plan_review_rounds "$plan_file")"
  log "Review rounds for this plan: $plan_review_iterations (max $MAX_REVIEW_ROUNDS)"

  local plan_base_branch
  plan_base_branch="$(parse_plan_base_branch "$plan_file")"
  if [ -n "$plan_base_branch" ]; then
    log "Base branch for this plan: $plan_base_branch"
  fi

  local work_id
  work_id="$(date '+%Y%m%d_%H%M%S')_${plan_slug}"
  local plan_work_dir="$WORK_DIR/$work_id"
  mkdir -p "$plan_work_dir"
  echo "$plan_review_iterations" > "$plan_work_dir/review_iterations"
  # Persist the base branch so resume paths (run_review_loop, push_and_open_prs)
  # can recover it without re-parsing the plan file.
  if [ -n "$plan_base_branch" ]; then
    echo "$plan_base_branch" > "$plan_work_dir/base_branch"
  fi

  local branch_name="owl/$plan_slug"
  local execution_base_file="$plan_work_dir/execution_base.tsv"
  : > "$execution_base_file"

  # Create a session ID for the Claude coder so the fix phase can --resume
  # into the same conversation and reuse file-read context. Sessions are
  # directory-scoped, so both the execution and fix calls must run from
  # the same cwd (the per-plan execution workspace).
  local coder_session_id=""
  if [ "$(normalize_provider "$IMPL_PROVIDER")" = "claude" ]; then
    coder_session_id="$(get_or_create_coder_session_id "$plan_work_dir")"
    log "Using Claude coder session: $coder_session_id"
  fi

  # ── Step 2: Create/reuse deterministic worktrees, then reset them to the base ──
  if ! ensure_plan_workspace "$plan_slug" "$plan_work_dir"; then
    log "Plan execution aborted before $IMPL_PROVIDER: failed to prepare worktrees."
    return 1
  fi
  reset_all_repos_to_base "$plan_base_branch" "1"

  while IFS= read -r -d '' repo_root; do
    local repo_name
    repo_name="$(basename "$repo_root")"
    local base_hash="NONE"
    if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      base_hash=$(git -C "$repo_root" rev-parse HEAD)
    fi
    printf '%s\t%s\t%s\n' "$repo_name" "$repo_root" "$base_hash" >> "$execution_base_file"
  done < <(find_target_repos)

  # ── Step 3: Execute plan ──
  log "[Step 3] Executing plan via $IMPL_PROVIDER ($IMPL_MODEL)..."
  cd "$EXECUTION_PROJECT_DIR"

  local plan_prompt_file="$plan_work_dir/plan_prompt.txt"
  cat > "$plan_prompt_file" <<PLANEOF
$plan_content

IMPORTANT INSTRUCTIONS:
- Do NOT commit, push, or create branches. Just write the code changes.
- The agent handles all git operations.
PLANEOF

  local coder_ack_file="$plan_work_dir/coder.ack"
  local coder_prompt_wrapped="$plan_work_dir/plan_prompt_wrapped.txt"
  prepend_ack_prompt "$coder_ack_file" "$plan_prompt_file" "$coder_prompt_wrapped"
  local coder_ack_watcher
  coder_ack_watcher=$(spawn_ack_watcher "coder" "$coder_ack_file")

  run_llm "$IMPL_PROVIDER" "$IMPL_MODEL" "$coder_prompt_wrapped" "$coder_session_id" "create"
  local exit_code=$?
  stop_ack_watcher "$coder_ack_watcher"
  local exec_output="$RETRY_OUTPUT"
  echo "$exec_output" >> "$LOG_FILE"
  echo "$exec_output" > "$plan_work_dir/execution.log"

  if [ $exit_code -ne 0 ] || [ -z "$exec_output" ] || echo "$exec_output" | grep -qi "^Execution error$"; then
    log "Plan execution failed (exit=$exit_code, output_len=${#exec_output}). Will retry next cycle."
    while IFS= read -r -d '' repo_root; do
      local repo_name
      repo_name="$(basename "$repo_root")"
      if ! git -C "$repo_root" diff --quiet 2>/dev/null || \
         ! git -C "$repo_root" diff --cached --quiet 2>/dev/null || \
         [ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
        log "  Discarding partial changes in $repo_name (caused by failed plan)"
        git -C "$repo_root" checkout -- . 2>>"$LOG_FILE"
        git -C "$repo_root" clean -fd 2>>"$LOG_FILE"
      fi
    done < <(find_target_repos)
    return 1
  fi

  log "Plan execution completed."

  # ── Step 4: Find dirty repos, create branch, commit ──
  log "[Step 4] Creating branch and committing changes..."
  : > "$plan_work_dir/review_input_1.tsv"
  if ! normalize_all_plan_repos \
    "$branch_name" \
    "[owl] ${plan_name%.md} — execution" \
    "$plan_work_dir" \
    "$plan_work_dir/review_input_1.tsv" \
    "create"; then
    log "Plan produced changes but repo normalization failed during execution commit phase. Will retry next cycle."
    switch_all_to_main
    return 1
  fi

  # ── Check that something was actually committed ──
  if [ ! -s "$plan_work_dir/review_input_1.tsv" ]; then
    # Distinguish "coder genuinely produced no changes" from "coder changed
    # files but git commit failed silently". Check if any repo still has a
    # dirty worktree — if so, the commit path above logged an ERROR and we
    # should retry, not mark done.
    local has_uncommitted_changes=false
    while IFS= read -r -d '' repo_root; do
      if ! git -C "$repo_root" diff --quiet 2>/dev/null || \
         ! git -C "$repo_root" diff --cached --quiet 2>/dev/null; then
        has_uncommitted_changes=true
        break
      fi
    done < <(find_target_repos)

    if $has_uncommitted_changes; then
      log "Plan produced changes but commits failed (see ERROR lines above). Will retry next cycle."
      switch_all_to_main
      return 1
    fi

    log "Plan produced no changes in any repo. Marking done with no-op summary."
    log "[Step 5] Switching all repos back to main..."
    switch_all_to_main
    cleanup_plan_workspace "$plan_slug" "$plan_work_dir"
    write_done_file "$plan_file" "$plan_name" "$plan_work_dir" 0 0 \
      "No repo changes were needed. The requested work appears to already be present on main."
    return 0
  fi

  # ── Step 5: Mark pending ──
  echo "$plan_file" > "$plan_work_dir/pending"
  echo "$branch_name" > "$plan_work_dir/branch"

  # ── Step 6: Review loop ──
  # Propagate non-zero so check_plans stops this cycle and next cycle resumes
  # the pending plan before picking up anything new.
  run_review_loop "$plan_file" "$plan_name" "$plan_work_dir" || return $?
}

write_done_file() {
  local plan_file="$1"
  local plan_name="$2"
  local plan_work_dir="$3"
  local reviews_successful="$4"
  local review_iterations="$5"
  local completion_note="${6:-}"

  mkdir -p "$PLAN_DIR/done"
  local done_name
  done_name="${plan_name%.md}_$(date '+%Y%m%d_%H%M%S').done.md"
  local done_path="$PLAN_DIR/done/$done_name"

  {
    strip_plan_frontmatter "$plan_file"
    echo ""
    echo "---"
    echo ""
    echo "## Execution Summary"
    echo ""
    echo "- **Completed:** $(date '+%Y-%m-%d %H:%M:%S')"
    echo "- **Review rounds:** $reviews_successful completed / $review_iterations total"
    if [ -n "$completion_note" ]; then
      echo "- **Outcome:** $completion_note"
    fi
    echo "- **Repos changed:**"
    if [ -f "$plan_work_dir/commits.tsv" ]; then
      while IFS=$'\t' read -r repo_name hash; do
        echo "  - \`$repo_name\` — commit \`$hash\`"
      done < "$plan_work_dir/commits.tsv"
    else
      echo "  - (none)"
    fi
    echo "- **Pull requests:**"
    if [ -f "$plan_work_dir/pull_requests.tsv" ]; then
      while IFS=$'\t' read -r repo_name pr_url; do
        echo "  - \`$repo_name\` — $pr_url"
      done < "$plan_work_dir/pull_requests.tsv"
    else
      echo "  - (none)"
    fi
    echo ""
    for r in $(seq 1 "$reviews_successful"); do
      echo "### Review Round $r"
      echo ""
      if [ -f "$plan_work_dir/combined_review_$r.txt" ]; then
        cat "$plan_work_dir/combined_review_$r.txt"
      else
        echo "(skipped)"
      fi
      echo ""
    done
  } > "$done_path"

  rm -f "$plan_file"
  rm -f "$plan_work_dir/pending"
  rm -f "$plan_work_dir/state"
  rm -f "$plan_work_dir/review_iterations"
  log "Wrote done file: $done_name"
}

# ─── Review loop ───
run_review_loop() {
  local plan_file="$1"
  local plan_name="$2"
  local plan_work_dir="$3"
  local plan_slug="${plan_name%.md}"

  if ! ensure_plan_workspace "$plan_slug" "$plan_work_dir"; then
    log "Review loop aborted: failed to prepare worktrees for $plan_name"
    return 1
  fi
  cd "$EXECUTION_PROJECT_DIR"

  # Load the coder session ID so the fix phase can --resume into the
  # execution conversation. Empty string if the execution didn't use Claude
  # or if the session file is missing (e.g. Codex execution).
  local coder_session_id=""
  coder_session_id="$(load_coder_session_id "$plan_work_dir")"

  # Load the plan so reviewers and the fix agent can see the author's intent.
  # Without this, reviewers flag deliberate changes as regressions because they
  # only see the diff, not what the plan asked for.
  local plan_content=""
  if [ -f "$plan_file" ]; then
    plan_content="$(strip_plan_frontmatter "$plan_file")"
  fi

  local branch_name=""
  [ -f "$plan_work_dir/branch" ] && branch_name=$(cat "$plan_work_dir/branch")

  # Per-plan review iteration count (persisted so resumes honor the plan's setting)
  local review_iterations=$REVIEW_ITERATIONS
  if [ -f "$plan_work_dir/review_iterations" ]; then
    local stored
    stored=$(cat "$plan_work_dir/review_iterations")
    if [[ "$stored" =~ ^[0-9]+$ ]] && [ "$stored" -ge 1 ]; then
      review_iterations=$stored
      if [ "$review_iterations" -gt "$MAX_REVIEW_ROUNDS" ]; then
        review_iterations=$MAX_REVIEW_ROUNDS
      fi
    fi
  fi

  # Ensure we're on the right branch
  if [ -n "$branch_name" ]; then
    while IFS= read -r -d '' repo_root; do
      if git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        git -C "$repo_root" checkout "$branch_name" 2>/dev/null
      fi
    done < <(find_target_repos)
  fi

  local reviews_done=0
  if [ -f "$plan_work_dir/state" ]; then
    reviews_done=$(sed -n 's/^reviews_done=\([0-9]*\)$/\1/p' "$plan_work_dir/state" 2>/dev/null)
    reviews_done=${reviews_done:-0}
  fi

  # Verify that each repo's branch tip still matches the HEAD hash we
  # recorded for the iteration we're about to run. If someone rewrote the
  # branch between cycles (interrupted commit, manual amend, force push,
  # accidental reset), the review would diff against a range that no longer
  # matches reality. Abort cleanly so the operator can investigate. On the
  # first call from execute_plan this is a tautology (the manifest was
  # written seconds ago), so the check is effectively a no-op except on
  # genuine resume.
  local resume_manifest="$plan_work_dir/review_input_$((reviews_done + 1)).tsv"
  if [ -s "$resume_manifest" ]; then
    local drift_detected=false
    while IFS=$'\t' read -r repo_name repo_root base_hash expected_head; do
      [ -n "$repo_root" ] || continue
      if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "[Resume] $repo_name: repo directory missing at $repo_root"
        drift_detected=true
        continue
      fi
      local actual_head
      actual_head=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || echo "")
      if [ "$actual_head" != "$expected_head" ]; then
        log "[Resume] $repo_name: HEAD=${actual_head:-<unknown>} does not match recorded manifest head=$expected_head"
        drift_detected=true
      fi
    done < "$resume_manifest"
    if $drift_detected; then
      log "Branch state drifted from recorded manifest. Leaving pending — investigate manually before the next cycle."
      {
        echo "plan_name=$plan_name"
        echo "plan_file=$plan_file"
        echo "branch_name=$branch_name"
        echo "failed_iteration=$((reviews_done + 1))"
        echo "total_iterations=$review_iterations"
        echo "reviews_done=$reviews_done"
        echo "reason=branch tip drifted from recorded manifest head"
        echo "aborted_at=$(date '+%Y-%m-%d %H:%M:%S')"
      } > "$plan_work_dir/pending_status"
      switch_all_to_main
      return 1
    fi
  fi

  local review_rounds_completed=$reviews_done
  local reviews_skipped=0

  # Tracks unrecoverable LLM failures (reviewer or fix rate-limited past
  # MAX_RETRIES). When set, the loop breaks and we bail out of the plan
  # without writing the done file or deleting the pending marker, so the
  # next owl cycle resumes this plan before picking up new ones.
  local had_unrecoverable_llm_failure=false
  local abort_reason=""
  local abort_iteration=0

  for i in $(seq $((reviews_done + 1)) "$review_iterations"); do
    log "-----------------------------------------"
    log "[Iteration $i/$review_iterations] Review phase"
    log "-----------------------------------------"

    local manifest="$plan_work_dir/review_input_$i.tsv"

    if [ ! -s "$manifest" ]; then
      log "No manifest for round $i. Skipping."
      reviews_skipped=$((reviews_skipped + 1))
      continue
    fi

    # Run deterministic tests before reviewers produce feedback. Any failures
    # get appended to the combined review below so the fix agent sees them
    # alongside LLM reviewer comments. Repos without OWL_TEST_CMD_<name> are
    # silently skipped; operators opt in per repo via .env.local.
    local tests_summary_file="$plan_work_dir/tests_$i.txt"
    local tests_ok=true
    run_deterministic_tests "$plan_work_dir" "$i" || tests_ok=false

    # Build review prompt — tell the LLM where to look, it reads the diff itself.
    # The plan is included so the reviewer can check the diff against the author's
    # intent instead of flagging deliberate changes as regressions.
    local review_prompt_file="$plan_work_dir/review_prompt_$i.txt"
    {
      echo "You are a code reviewer. Review the changes in the commits listed below for bugs, security issues, code quality problems, and correctness. Judge the diff against the plan below — if a change looks surprising but matches what the plan explicitly asked for, that is NOT a bug and must not be flagged. Only flag things that are wrong relative to the plan or introduce genuine defects (security, correctness, crashes, obviously broken logic). Be concise — return only actionable fixes, no praise. If nothing needs fixing, respond with exactly: LGTM"
      echo ""
      echo "## Plan being implemented"
      echo ""
      if [ -n "$plan_content" ]; then
        echo "$plan_content"
      else
        echo "(plan file unavailable)"
      fi
      echo ""
      echo "## Commits to review"
      echo ""
      echo "Use git diff to inspect the changes. Here are the repos and commit ranges to review:"
      echo ""
      while IFS=$'\t' read -r repo_name repo_root base head; do
        if [ "$base" != "NONE" ] && [ -n "$base" ]; then
          echo "- Repo: $repo_name (path: $repo_root) — run: git -C $repo_root diff $base $head"
        else
          echo "- Repo: $repo_name (path: $repo_root) — run: git -C $repo_root show $head"
        fi
      done < "$manifest"
    } > "$review_prompt_file"

    # Launch reviewers
    local reviewer1_file="$plan_work_dir/reviewer1_$i.txt"
    local reviewer2_file="$plan_work_dir/reviewer2_$i.txt"
    local reviewer1_ok=true reviewer2_ok=true
    local reviewer1_enabled=true reviewer2_enabled=true
    reviewer_slot_enabled "$REVIEWER1_PROVIDER" "$REVIEWER1_MODEL" || reviewer1_enabled=false
    reviewer_slot_enabled "$REVIEWER2_PROVIDER" "$REVIEWER2_MODEL" || reviewer2_enabled=false

    local reviewer1_ack_file="$plan_work_dir/reviewer1_$i.ack"
    local reviewer2_ack_file="$plan_work_dir/reviewer2_$i.ack"
    local reviewer1_prompt_wrapped="$plan_work_dir/reviewer1_prompt_$i.txt"
    local reviewer2_prompt_wrapped="$plan_work_dir/reviewer2_prompt_$i.txt"
    local reviewer1_ack_watcher=""
    local reviewer2_ack_watcher=""
    if $reviewer1_enabled; then
      prepend_ack_prompt "$reviewer1_ack_file" "$review_prompt_file" "$reviewer1_prompt_wrapped"
    fi
    if $reviewer2_enabled; then
      prepend_ack_prompt "$reviewer2_ack_file" "$review_prompt_file" "$reviewer2_prompt_wrapped"
    fi

    if [ "$REVIEW_MODE" = "parallel" ]; then
      log "Spawning reviewers in parallel..."

      local reviewer1_pid=""
      local reviewer2_pid=""

      if $reviewer1_enabled; then
        (
          rc=0
          run_llm "$REVIEWER1_PROVIDER" "$REVIEWER1_MODEL" "$reviewer1_prompt_wrapped" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer1_file" 2>&1 &
        reviewer1_pid=$!
        reviewer1_ack_watcher=$(spawn_ack_watcher "reviewer1 $REVIEWER1_LABEL" "$reviewer1_ack_file")
      else
        echo "LGTM" > "$reviewer1_file"
      fi

      if $reviewer2_enabled; then
        (
          rc=0
          run_llm "$REVIEWER2_PROVIDER" "$REVIEWER2_MODEL" "$reviewer2_prompt_wrapped" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer2_file" 2>&1 &
        reviewer2_pid=$!
        reviewer2_ack_watcher=$(spawn_ack_watcher "reviewer2 $REVIEWER2_LABEL" "$reviewer2_ack_file")
      else
        echo "LGTM" > "$reviewer2_file"
      fi

      if [ -n "$reviewer1_pid" ]; then
        wait "$reviewer1_pid" || reviewer1_ok=false
      fi
      stop_ack_watcher "$reviewer1_ack_watcher"
      log "$REVIEWER1_LABEL review complete (enabled=$reviewer1_enabled success=$reviewer1_ok)."

      if [ -n "$reviewer2_pid" ]; then
        wait "$reviewer2_pid" || reviewer2_ok=false
      fi
      stop_ack_watcher "$reviewer2_ack_watcher"
      log "$REVIEWER2_LABEL review complete (enabled=$reviewer2_enabled success=$reviewer2_ok)."

    else
      log "Running reviewers sequentially..."

      if $reviewer1_enabled; then
        log "Running $REVIEWER1_LABEL reviewer..."
        reviewer1_ack_watcher=$(spawn_ack_watcher "reviewer1 $REVIEWER1_LABEL" "$reviewer1_ack_file")
        (
          rc=0
          run_llm "$REVIEWER1_PROVIDER" "$REVIEWER1_MODEL" "$reviewer1_prompt_wrapped" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer1_file" 2>&1 || reviewer1_ok=false
        stop_ack_watcher "$reviewer1_ack_watcher"
      else
        echo "LGTM" > "$reviewer1_file"
      fi
      log "$REVIEWER1_LABEL review complete (enabled=$reviewer1_enabled success=$reviewer1_ok)."

      if $reviewer2_enabled; then
        log "Running $REVIEWER2_LABEL reviewer..."
        reviewer2_ack_watcher=$(spawn_ack_watcher "reviewer2 $REVIEWER2_LABEL" "$reviewer2_ack_file")
        (
          rc=0
          run_llm "$REVIEWER2_PROVIDER" "$REVIEWER2_MODEL" "$reviewer2_prompt_wrapped" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer2_file" 2>&1 || reviewer2_ok=false
        stop_ack_watcher "$reviewer2_ack_watcher"
      else
        echo "LGTM" > "$reviewer2_file"
      fi
      log "$REVIEWER2_LABEL review complete (enabled=$reviewer2_enabled success=$reviewer2_ok)."
    fi

    if ! $reviewer1_ok && ! $reviewer2_ok; then
      # Peek at the last non-empty line of each reviewer's captured output so
      # operators can tell rate-limit exhaustion from a tool crash (missing
      # binary, auth error, etc.) without grepping the .work/ logs.
      local reviewer1_tail="" reviewer2_tail=""
      [ -f "$reviewer1_file" ] && reviewer1_tail=$(tail -n 10 "$reviewer1_file" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 1 | head -c 200)
      [ -f "$reviewer2_file" ] && reviewer2_tail=$(tail -n 10 "$reviewer2_file" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 1 | head -c 200)
      log "All enabled reviewers failed for iteration $i (retry_on_limit exhausted or non-recoverable error after up to $MAX_RETRIES attempts)."
      [ -n "$reviewer1_tail" ] && log "  $REVIEWER1_LABEL tail: $reviewer1_tail"
      [ -n "$reviewer2_tail" ] && log "  $REVIEWER2_LABEL tail: $reviewer2_tail"
      had_unrecoverable_llm_failure=true
      abort_reason="all reviewers failed (rate-limited or tool crash)"
      abort_iteration=$i
      break
    fi

    # Strip the codex transcript noise from each reviewer's capture and
    # keep only the verdict block. See extract_reviewer_verdict for the
    # full rationale: this fixes the LGTM gate (which was failing strict
    # equality against ~500KB of transcript) AND keeps the fix LLM's
    # prompt small enough to fit Claude's context window across multiple
    # iterations.
    local reviewer1_review=""
    local reviewer2_review=""
    [ -f "$reviewer1_file" ] && reviewer1_review=$(extract_reviewer_verdict "$reviewer1_file")
    [ -f "$reviewer2_file" ] && reviewer2_review=$(extract_reviewer_verdict "$reviewer2_file")

    local combined_review="## $REVIEWER1_LABEL Review

$reviewer1_review

## $REVIEWER2_LABEL Review

$reviewer2_review"

    if [ -s "$tests_summary_file" ]; then
      combined_review="$combined_review

## Deterministic test failures

These suites were run automatically by Owl after the previous commit and failed. The fix MUST make them pass again; treat this as non-negotiable, ranking higher than any reviewer preference that contradicts a failing test. Do not delete, skip, or trivially mock tests just to make them pass — investigate the failure and fix the actual cause.

$(cat "$tests_summary_file")"
    fi

    echo "$combined_review" > "$plan_work_dir/combined_review_$i.txt"
    review_rounds_completed=$i

    local reviewer1_trimmed reviewer2_trimmed
    reviewer1_trimmed=$(echo "$reviewer1_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    reviewer2_trimmed=$(echo "$reviewer2_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if $tests_ok && \
       { ! $reviewer1_enabled || [ "$reviewer1_trimmed" = "LGTM" ]; } && \
       { ! $reviewer2_enabled || [ "$reviewer2_trimmed" = "LGTM" ]; }; then
      log "All enabled reviewers say LGTM and deterministic tests passed. No fixes needed."
      break
    fi
    if ! $tests_ok; then
      log "Deterministic tests failed — fix phase will address them alongside reviewer feedback."
    fi

    # ── Fix phase ──
    log "[Iteration $i/$review_iterations] Fix phase"

    local fix_prompt_file="$plan_work_dir/fix_prompt_$i.txt"
    cat > "$fix_prompt_file" <<FIXEOF
You received the following code review feedback on recent changes in this project. Apply the necessary fixes. Only change what the review asks for — do not refactor unrelated code.

How to handle conflicts between the plan and the reviewers:

The plan below describes the intended behavior and is the default source of truth. Reviewer comments should be weighed against it, not applied blindly.

1. If a reviewer comment is consistent with the plan, apply the fix.
2. If a reviewer comment CONTRADICTS the plan (for example, asks you to revert a change that the plan explicitly requested), do not apply it automatically. First reason about whether the reviewer has actually identified a real defect the plan overlooked:
   - Does following the plan as-written cause a concrete bug, crash, data loss, security issue, incorrect result, or broken build?
   - Is the reviewer's suggested fix the right way to address that specific defect?
   If BOTH are clearly yes, deviate from the plan and apply the reviewer's fix. In that case, add a brief comment at the change site (or in your final summary) stating which plan instruction you deviated from and why the reviewer's concern was strong enough to override it.
   If the contradiction is a style preference, a hedged question, a refactor suggestion, or anything short of a concrete defect, follow the plan. For hedged questions ("if X is intentional, document it; if not, revert") where the plan confirms X is intentional, prefer a short comment or docstring over reverting.
3. When in doubt, the plan wins. Deviation is reserved for cases where the reviewer catches a real bug the plan got wrong, not for taste-level disagreement.

IMPORTANT: Do NOT commit, push, or create branches. Just write the code fixes.

## Plan being implemented

${plan_content:-(plan file unavailable)}

## Review Feedback

$combined_review
FIXEOF

    log "Applying fixes via $FIX_PROVIDER ($FIX_MODEL)..."
    local fix_rc=0
    local fix_ack_file="$plan_work_dir/fix_$i.ack"
    local fix_prompt_wrapped="$plan_work_dir/fix_prompt_wrapped_$i.txt"
    prepend_ack_prompt "$fix_ack_file" "$fix_prompt_file" "$fix_prompt_wrapped"
    local fix_ack_watcher
    fix_ack_watcher=$(spawn_ack_watcher "fix-agent" "$fix_ack_file")
    if [ "$(normalize_provider "$FIX_PROVIDER")" = "claude" ] && [ -n "$coder_session_id" ]; then
      log "Resuming Claude coder session for fixes: $coder_session_id"
      run_llm "$FIX_PROVIDER" "$FIX_MODEL" "$fix_prompt_wrapped" "$coder_session_id" "resume" || fix_rc=$?
    else
      run_llm "$FIX_PROVIDER" "$FIX_MODEL" "$fix_prompt_wrapped" || fix_rc=$?
    fi
    stop_ack_watcher "$fix_ack_watcher"
    echo "$RETRY_OUTPUT" | tee -a "$LOG_FILE" > "$plan_work_dir/fixes_$i.log"
    if [ "$fix_rc" -ne 0 ]; then
      # Same as the reviewer abort: surface the last non-empty output line so
      # the operator can distinguish rate-limit exhaustion from tool crashes.
      local fix_tail=""
      fix_tail=$(printf '%s\n' "$RETRY_OUTPUT" | grep -v '^[[:space:]]*$' | tail -n 1 | head -c 200)
      log "Fix LLM failed for iteration $i (exit=$fix_rc, retry_on_limit exhausted or non-recoverable error after up to $MAX_RETRIES attempts)."
      [ -n "$fix_tail" ] && log "  fix tail: $fix_tail"
      had_unrecoverable_llm_failure=true
      abort_reason="fix LLM failed (rate-limited or tool crash)"
      abort_iteration=$i
      break
    fi

    local next_manifest="$plan_work_dir/review_input_$((i + 1)).tsv"
    cp "$manifest" "$next_manifest" 2>/dev/null || : > "$next_manifest"
    if ! normalize_all_plan_repos \
      "$branch_name" \
      "[owl] ${plan_name%.md} — review fix iteration $i" \
      "$plan_work_dir" \
      "$next_manifest" \
      "reuse"; then
      log "Fix phase left repos in a dirty or uncommittable state. Will retry next cycle."
      switch_all_to_main
      return 1
    fi

    review_rounds_completed=$i
    echo "reviews_done=$i" > "$plan_work_dir/state"
  done

  # ── Unrecoverable LLM failure? Bail out, keep pending for next cycle. ──
  if $had_unrecoverable_llm_failure; then
    local reviews_done_now=0
    if [ -f "$plan_work_dir/state" ]; then
      reviews_done_now=$(sed -n 's/^reviews_done=\([0-9]*\)$/\1/p' "$plan_work_dir/state" 2>/dev/null || echo 0)
      reviews_done_now=${reviews_done_now:-0}
    fi

    log "========================================="
    log "Plan '$plan_name' aborted mid-review."
    log "  reason:              $abort_reason"
    log "  failed at iteration: $abort_iteration / $review_iterations"
    log "  reviews completed:   $reviews_done_now"
    log "  branch:              $branch_name"
    log "Pending marker preserved — next owl cycle will resume this plan before picking up new ones."
    log "========================================="

    {
      echo "plan_name=$plan_name"
      echo "plan_file=$plan_file"
      echo "branch_name=$branch_name"
      echo "failed_iteration=$abort_iteration"
      echo "total_iterations=$review_iterations"
      echo "reviews_done=$reviews_done_now"
      echo "reason=$abort_reason"
      echo "aborted_at=$(date '+%Y-%m-%d %H:%M:%S')"
    } > "$plan_work_dir/pending_status"

    # Detach execution worktrees away from the plan branch so the next cycle
    # resumes from a neutral checkout.
    switch_all_to_main
    return 1
  fi

  # ── Step 7: Push and open PRs ──
  local reviews_successful=$(( review_rounds_completed > reviews_skipped ? review_rounds_completed - reviews_skipped : 0 ))
  if [ -n "$branch_name" ]; then
    if ! normalize_all_plan_repos \
      "$branch_name" \
      "[owl] ${plan_name%.md} — pre-push normalization" \
      "$plan_work_dir" \
      "$plan_work_dir/review_input_$((review_rounds_completed + 1)).tsv" \
      "reuse"; then
      log "Pre-push normalization failed. Will retry next cycle."
      switch_all_to_main
      return 1
    fi
    log "[Step 7] Pushing branches and opening PRs..."
    if ! push_and_open_prs "$branch_name" "$plan_name" "$plan_file" "$plan_work_dir" "$reviews_successful" "$review_iterations"; then
      log "PR creation failed for at least one touched repo. Will retry next cycle."
      switch_all_to_main
      return 1
    fi
  fi

  # ── Step 8: Switch back to main ──
  log "[Step 8] Switching all repos back to main..."
  switch_all_to_main
  cleanup_plan_workspace "$plan_slug" "$plan_work_dir"

  # ── Step 9: Write done file ──
  log "========================================="
  log "Plan '$plan_name' completed."
  log "========================================="
  write_done_file "$plan_file" "$plan_name" "$plan_work_dir" "$reviews_successful" "$review_iterations"
}

# ─── Resume pending reviews ───
resume_pending_reviews() {
  local resumed=false
  for pending_file in "$WORK_DIR"/*/pending; do
    [ -f "$pending_file" ] || continue
    local plan_work_dir plan_name
    plan_work_dir="$(dirname "$pending_file")"
    local plan_file
    plan_file="$(cat "$pending_file")"
    plan_name="$(basename "$plan_file")"

    if [ ! -f "$plan_file" ]; then
      log "Pending review for '$plan_name' but plan file is gone. Cleaning up."
      cleanup_plan_workspace "${plan_name%.md}" "$plan_work_dir"
      rm -f "$pending_file"
      continue
    fi

    if [ -f "$plan_work_dir/pending_status" ]; then
      log "Resuming pending review for: $plan_name (previously aborted mid-review)"
      while IFS= read -r status_line; do
        [ -n "$status_line" ] && log "  $status_line"
      done < "$plan_work_dir/pending_status"
      # Clear the prior status; run_review_loop will write a fresh one if it
      # aborts again this cycle.
      rm -f "$plan_work_dir/pending_status"
    else
      log "Resuming pending review for: $plan_name"
    fi

    local rrl_rc=0
    run_review_loop "$plan_file" "$plan_name" "$plan_work_dir" || rrl_rc=$?
    resumed=true

    # If resume aborted again (rate limits still biting), stop the cycle
    # instead of hammering every pending plan with the same failure.
    if [ "$rrl_rc" -ne 0 ] || [ -f "$plan_work_dir/pending_status" ]; then
      log "Plan '$plan_name' still pending after resume attempt. Stopping cycle; will retry next cycle."
      return 0
    fi
  done
  if $resumed; then return 0; else return 1; fi
}

# ─── Main check loop ───
check_plans() {
  log "Checking for plans in $PLAN_DIR..."

  if resume_pending_reviews; then
    log "Resumed pending reviews. Will check for new plans next cycle."
    return
  fi

  local found=0
  local skipped_low=0

  # Two-pass drain: always run normal-priority plans first in filename order,
  # then drain low-priority plans in filename order. This keeps high-value work
  # from getting stuck behind a cheap nice-to-have that happens to have a
  # smaller numeric prefix. When SKIP_LOW_PRIORITY=1, the second pass skips
  # every low-priority plan instead of executing it.

  # ── Pass 1: normal-priority plans ──
  while IFS= read -r -d '' plan_file; do
    [ -f "$plan_file" ] || continue
    local plan_priority
    plan_priority="$(parse_plan_priority "$plan_file")"
    if [ "$plan_priority" = "low" ]; then
      continue
    fi

    found=1
    if ! execute_plan "$plan_file"; then
      log "Plan failed — will retry next cycle. Stopping current cycle."
      return
    fi
  done < <(find "$PLAN_DIR" -maxdepth 1 -name "*.md" -print0 2>/dev/null | sort -z)

  # ── Pass 2: low-priority plans (drain after all normal plans have run) ──
  while IFS= read -r -d '' plan_file; do
    [ -f "$plan_file" ] || continue
    local plan_priority
    plan_priority="$(parse_plan_priority "$plan_file")"
    if [ "$plan_priority" != "low" ]; then
      continue
    fi

    if [ "$SKIP_LOW_PRIORITY" = "1" ]; then
      log "  skipping low-priority plan: $(basename "$plan_file") (SKIP_LOW_PRIORITY=1)"
      skipped_low=$((skipped_low + 1))
      continue
    fi

    found=1
    if ! execute_plan "$plan_file"; then
      log "Plan failed — will retry next cycle. Stopping current cycle."
      return
    fi
  done < <(find "$PLAN_DIR" -maxdepth 1 -name "*.md" -print0 2>/dev/null | sort -z)

  if [ $found -eq 0 ]; then
    if [ $skipped_low -gt 0 ]; then
      log "No eligible plans found ($skipped_low low-priority plan(s) skipped)."
    else
      log "No plans found."
    fi
  fi
}

# ─── Validate plan (dry-run) ───
# Invoked when --validate <plan> is passed. Parses frontmatter and enumerates
# what the agent would do without calling any LLM or touching git.
# NOTE: keep this in sync with execute_plan() -- if a new frontmatter field is
# added there, add it here too so --validate reports it accurately.
validate_plan() {
  local plan_file="$VALIDATE_PLAN_FILE"

  # 1. Confirm the file exists and print its absolute path
  if [ ! -f "$plan_file" ]; then
    echo "error: plan file not found: $plan_file" >&2
    return 1
  fi
  local abs_path
  abs_path="$(cd "$(dirname "$plan_file")" && pwd)/$(basename "$plan_file")"
  echo "Plan file: $abs_path"
  echo ""

  # 2. Parse frontmatter fields
  local review_rounds priority base_branch
  review_rounds="$(parse_plan_review_rounds "$plan_file")"
  priority="$(parse_plan_priority "$plan_file")"
  base_branch="$(parse_plan_base_branch "$plan_file")"

  echo "Frontmatter:"
  echo "  review-rounds : $review_rounds"
  echo "  priority      : ${priority:-normal}"
  echo "  base-branch   : ${base_branch:-(none)}"
  echo ""

  # 3. Compute target branch name
  local plan_name
  plan_name="$(basename "$plan_file")"
  local branch_name="owl/${plan_name%.md}"
  echo "Target branch: $branch_name"
  echo ""

  # 4. Enumerate target repos and (5) check base-branch availability
  echo "Target repos:"
  local found_repos=0
  while IFS= read -r -d '' repo_root; do
    local repo_name
    repo_name="$(basename "$repo_root")"
    echo "  - $repo_name ($repo_root)"
    found_repos=$((found_repos + 1))

    if [ -n "$base_branch" ]; then
      # Check local remote-tracking ref only (no fetch -- validation must not touch git)
      if git -C "$repo_root" rev-parse --verify "refs/remotes/origin/$base_branch" >/dev/null 2>&1; then
        echo "      would use base: $base_branch"
      else
        echo "      would fall back to main (base '$base_branch' not cached locally)"
      fi
    fi
  done < <(find_source_target_repos)

  if [ "$found_repos" -eq 0 ]; then
    echo "  (none found -- check OWL_TARGET_REPOS)"
  fi
  echo ""

  echo "validation OK -- run without --validate to execute"
  return 0
}

# ─── Doctor (preflight environment check) ───
# Invoked when --doctor is passed. Verifies that the CLIs required by the
# configured providers are installed, that `gh` is authed for PR creation,
# and that every repo in OWL_TARGET_REPOS actually exists next to owl/.
# Does NOT call any LLM, run git, or acquire the lock file. Designed to be
# the first thing a new user runs on a fresh machine.
run_doctor() {
  local problems=0
  local warnings=0

  echo "Owl doctor -- checking environment"
  echo "  PROJECT_DIR : $PROJECT_DIR"
  echo "  PLAN_DIR    : $PLAN_DIR"
  echo ""

  # ── 1. Provider CLIs ──
  # Only check for the CLIs the configured roles actually use. A user who only
  # runs Claude shouldn't be scolded about a missing codex binary.
  local need_claude=false need_codex=false
  local role
  for role in "$IMPL_PROVIDER" "$FIX_PROVIDER" "$REVIEWER1_PROVIDER" "$REVIEWER2_PROVIDER"; do
    case "$role" in
      claude) need_claude=true ;;
      codex)  need_codex=true ;;
    esac
  done

  echo "Provider CLIs:"
  if $need_claude; then
    if command -v claude >/dev/null 2>&1; then
      echo "  ok    claude        ($(command -v claude))"
    else
      echo "  FAIL  claude        not found on PATH -- required by a configured role"
      problems=$((problems + 1))
    fi
  else
    echo "  skip  claude        (no role configured to use it)"
  fi
  if $need_codex; then
    if command -v codex >/dev/null 2>&1; then
      echo "  ok    codex         ($(command -v codex))"
    else
      echo "  FAIL  codex         not found on PATH -- required by a configured role"
      problems=$((problems + 1))
    fi
  else
    echo "  skip  codex         (no role configured to use it)"
  fi
  echo ""

  # ── 2. gh CLI + auth (always required for PR creation) ──
  echo "GitHub CLI:"
  if command -v gh >/dev/null 2>&1; then
    echo "  ok    gh            ($(command -v gh))"
    if gh auth status >/dev/null 2>&1; then
      echo "  ok    gh auth       authenticated"
    else
      echo "  FAIL  gh auth       not authenticated -- run 'gh auth login'"
      problems=$((problems + 1))
    fi
  else
    echo "  FAIL  gh            not found on PATH -- required for 'gh pr create'"
    problems=$((problems + 1))
  fi
  echo ""

  # ── 3. OWL_TARGET_REPOS + per-repo existence check ──
  echo "Target repos:"
  if [ -z "$TARGET_REPOS" ]; then
    echo "  FAIL  OWL_TARGET_REPOS is not set"
    echo "        Set it in .env.local next to src/owl.sh, e.g.:"
    echo "        OWL_TARGET_REPOS=\"my-repo other-repo\""
    problems=$((problems + 1))
  else
    echo "  ok    OWL_TARGET_REPOS=\"$TARGET_REPOS\""
    local repo_name repo_path
    for repo_name in $TARGET_REPOS; do
      repo_path="$PROJECT_DIR/$repo_name"
      if git -C "$repo_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "    ok  $repo_name -> $repo_path"
      else
        echo "    FAIL $repo_name: $repo_path is not a git repo"
        echo "         (expected a sibling of the owl/ checkout)"
        problems=$((problems + 1))
      fi
    done
  fi
  echo ""

  # ── 4. Plan directory ──
  echo "Plan directory:"
  if [ -d "$PLAN_DIR" ]; then
    local pending
    pending=$(find "$PLAN_DIR" -maxdepth 1 -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
    echo "  ok    $PLAN_DIR ($pending plan(s) queued)"
  else
    echo "  warn  $PLAN_DIR does not exist -- create it or drop a plan in"
    echo "        'cp examples/001-touch-readme.md plan/' gets you a working sample"
    warnings=$((warnings + 1))
  fi
  echo ""

  # ── Summary ──
  echo "========================================="
  if [ "$problems" -eq 0 ]; then
    if [ "$warnings" -eq 0 ]; then
      echo "All checks passed. Ready to run ./src/owl.sh"
    else
      echo "All required checks passed ($warnings warning(s)). Ready to run ./src/owl.sh"
    fi
    return 0
  fi
  echo "$problems problem(s), $warnings warning(s). Fix the FAILs above, then re-run --doctor."
  return 1
}

# ─── Main ───
# Guarded so tests can source this file to exercise individual functions
# without running the lock/loop. Matches the Python `if __name__ == "__main__"`
# idiom: when executed directly, $0 equals ${BASH_SOURCE[0]}; when sourced,
# they differ.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "$DOCTOR_MODE" = "1" ]; then
    run_doctor
    exit $?
  fi

  if [ -n "$VALIDATE_PLAN_FILE" ]; then
    validate_plan
    exit $?
  fi

  acquire_lock

  log "Dev Agent started. Checking every $(format_poll_interval "$POLL_INTERVAL_SECONDS")."
  log "Plan directory: $PLAN_DIR"
  log "Project directory: $PROJECT_DIR"
  log "Review iterations: $REVIEW_ITERATIONS (default; plans may override up to $MAX_REVIEW_ROUNDS via frontmatter)"
  if [ "$SKIP_LOW_PRIORITY" = "1" ]; then
    log "Low-priority plans: SKIPPING (plans with 'priority: low' in frontmatter will be bypassed)"
  else
    log "Low-priority plans: INCLUDED"
  fi

  while true; do
    check_plans
    log "Sleeping $(format_poll_interval "$POLL_INTERVAL_SECONDS")..."
    sleep "$POLL_INTERVAL_SECONDS"
  done
fi
