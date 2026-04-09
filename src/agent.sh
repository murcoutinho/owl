#!/bin/bash
#
# Dev Agent — checks for plans every 10 minutes, executes via Claude Code,
# then runs 2 review-fix iterations (Codex + Claude Code review → Claude Code fix)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_DIR="$SCRIPT_DIR/../plan"
LOG_FILE="$SCRIPT_DIR/../agent.log"
WORK_DIR="$SCRIPT_DIR/../.work"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOCK_FILE="$SCRIPT_DIR/../.agent.lock"
REVIEW_ITERATIONS=2
RETRY_WAIT=600  # 10 minutes between retries
MAX_RETRIES=50  # give up after ~8 hours of retrying

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

acquire_lock() {
  if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    echo "Another agent instance is running (lock: $LOCK_FILE). Exiting."
    exit 1
  fi
  trap 'rm -rf "$LOCK_FILE"' EXIT INT TERM
}

is_rate_limited() {
  local output="$1"
  echo "$output" | grep -qiE "rate.?limit|too many requests|429|overloaded|capacity|quota exceeded|try again"
}

# Fix #2: Properly capture exit code (|| true swallows it via PIPESTATUS)
retry_on_limit() {
  local desc="$1"
  shift
  local attempt=0

  while true; do
    attempt=$((attempt + 1))
    local rc=0
    RETRY_OUTPUT=$("$@" 2>&1) || rc=$?

    if [ $rc -eq 0 ] && ! is_rate_limited "$RETRY_OUTPUT"; then
      return 0
    fi

    if is_rate_limited "$RETRY_OUTPUT"; then
      if [ $attempt -ge $MAX_RETRIES ]; then
        log "RATE LIMIT: $desc — gave up after $attempt attempts."
        return 1
      fi
      log "RATE LIMIT: $desc — attempt $attempt hit rate limit. Retrying in $((RETRY_WAIT / 60)) minutes..."
      sleep $RETRY_WAIT
    else
      return $rc
    fi
  done
}

apply_fixes() {
  local review_feedback="$1"
  local fix_prompt="You received the following code review feedback on recent changes in this project. Apply the necessary fixes. Only change what the review asks for — do not refactor unrelated code.

## Review Feedback

$review_feedback"

  log "Applying fixes via Claude Code..."
  retry_on_limit "Apply fixes" claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 "$fix_prompt"
  local rc=$?
  echo "$RETRY_OUTPUT" | tee -a "$LOG_FILE"
  return $rc
}

# Fix #4: Accept plan_work_dir as explicit $3 parameter instead of relying on dynamic scoping
commit_all_repos() {
  local msg="$1"
  local manifest_file="$2"
  local _pwd="$3"

  : > "$manifest_file"

  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    local repo_name="$(basename "$repo_root")"

    if git -C "$repo_root" diff --quiet HEAD 2>/dev/null && \
       [ -z "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
      continue
    fi

    local before=""
    if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      before="$(git -C "$repo_root" rev-parse HEAD)"
    fi

    log "Committing changes in $repo_name..."
    git -C "$repo_root" add -A 2>/dev/null

    if git -C "$repo_root" commit -m "[dev-agent] $msg" 2>/dev/null; then
      local after
      after="$(git -C "$repo_root" rev-parse HEAD)"
      local short_hash
      short_hash="$(git -C "$repo_root" rev-parse --short HEAD)"
      log "Committed in $repo_name ($short_hash)."

      printf '%s\t%s\t%s\t%s\n' "$repo_name" "$repo_root" "$before" "$after" >> "$manifest_file"
      printf '%s\t%s\n' "$repo_name" "$short_hash" >> "$_pwd/commits.tsv"
    else
      log "Nothing to commit in $repo_name."
    fi
  done < <(find "$PROJECT_DIR" -maxdepth 2 -name ".git" -type d -print0 2>/dev/null)
}

build_diff_from_manifest() {
  local manifest="$1"
  local diff=""

  [ -f "$manifest" ] || return 0

  while IFS=$'\t' read -r repo_name repo_root base head; do
    local repo_diff=""
    if [ -n "$base" ]; then
      repo_diff="$(git -C "$repo_root" diff "$base" "$head" 2>/dev/null)"
    else
      repo_diff="$(git -C "$repo_root" show --format= "$head" 2>/dev/null)"
    fi

    if [ -n "$repo_diff" ]; then
      diff="${diff}
## Repo: ${repo_name}
${repo_diff}"
    fi
  done < "$manifest"

  echo "$diff"
}

run_review_loop() {
  local plan_file="$1"
  local plan_name="$2"
  local plan_work_dir="$3"

  # Fix #7: Use sed instead of grep -P (not available on macOS)
  local reviews_done=0
  if [ -f "$plan_work_dir/state" ]; then
    reviews_done=$(sed -n 's/^reviews_done=\([0-9]*\)$/\1/p' "$plan_work_dir/state" 2>/dev/null)
    reviews_done=${reviews_done:-0}
  fi

  local review_rounds_completed=$reviews_done

  # Fix #6: On resume, commit any uncommitted changes from an interrupted fix phase
  if [ "$reviews_done" -gt 0 ]; then
    local has_uncommitted=false
    while IFS= read -r -d '' repo_dir; do
      local repo_root="$(dirname "$repo_dir")"
      if ! git -C "$repo_root" diff --quiet HEAD 2>/dev/null || \
         [ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
        has_uncommitted=true
        break
      fi
    done < <(find "$PROJECT_DIR" -maxdepth 2 -name ".git" -type d -print0 2>/dev/null)

    if $has_uncommitted; then
      log "Found uncommitted changes from interrupted fix phase. Committing..."
      commit_all_repos "$plan_name — interrupted fix recovery" "$plan_work_dir/review_input_$((reviews_done + 1)).tsv" "$plan_work_dir"
    fi
  fi

  for i in $(seq $((reviews_done + 1)) $REVIEW_ITERATIONS); do
    log "-----------------------------------------"
    log "[Iteration $i/$REVIEW_ITERATIONS] Review phase"
    log "-----------------------------------------"

    local manifest="$plan_work_dir/review_input_$i.tsv"
    local diff
    diff="$(build_diff_from_manifest "$manifest")"

    if [ -z "$diff" ]; then
      log "No diff found to review (missing or empty manifest for round $i). Skipping this round."
      continue
    fi

    local review_prompt_text="You are a code reviewer. Review the following git diff for bugs, security issues, code quality problems, and correctness. Be concise — return only actionable fixes, no praise. If nothing needs fixing, respond with exactly: LGTM

\`\`\`diff
$diff
\`\`\`"

    # Fix #3: Propagate reviewer exit code from subshell
    log "Spawning Codex reviewer..."
    local codex_review_file="$plan_work_dir/codex_review_$i.txt"
    (
      rc=0
      retry_on_limit "Codex review" codex exec --full-auto --skip-git-repo-check "$review_prompt_text" || rc=$?
      echo "$RETRY_OUTPUT"
      exit $rc
    ) > "$codex_review_file" 2>&1 &
    local codex_pid=$!

    log "Spawning Claude Code reviewer..."
    local claude_review_file="$plan_work_dir/claude_review_$i.txt"
    (
      rc=0
      retry_on_limit "Claude review" claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 "$review_prompt_text" || rc=$?
      echo "$RETRY_OUTPUT"
      exit $rc
    ) > "$claude_review_file" 2>&1 &
    local claude_pid=$!

    local codex_ok=true claude_ok=true
    wait $codex_pid || codex_ok=false
    log "Codex review complete (success=$codex_ok)."
    wait $claude_pid || claude_ok=false
    log "Claude Code review complete (success=$claude_ok)."

    if ! $codex_ok && ! $claude_ok; then
      log "Both reviewers failed. Skipping fix phase for iteration $i."
      continue
    fi

    local codex_review=""
    local claude_review=""
    [ -f "$codex_review_file" ] && codex_review=$(cat "$codex_review_file")
    [ -f "$claude_review_file" ] && claude_review=$(cat "$claude_review_file")

    local combined_review="## Codex Review

$codex_review

## Claude Code Review

$claude_review"

    echo "$combined_review" > "$plan_work_dir/combined_review_$i.txt"
    review_rounds_completed=$i

    local codex_trimmed claude_trimmed
    codex_trimmed=$(echo "$codex_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    claude_trimmed=$(echo "$claude_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if [ "$codex_trimmed" = "LGTM" ] && [ "$claude_trimmed" = "LGTM" ]; then
      log "Both reviewers say LGTM. No fixes needed."
      break
    fi

    log "[Iteration $i/$REVIEW_ITERATIONS] Fix phase"

    local fix_output
    fix_output=$(cd "$PROJECT_DIR" && apply_fixes "$combined_review")
    echo "$fix_output" > "$plan_work_dir/fixes_$i.log"

    log "Fixes applied for iteration $i."
    commit_all_repos "$plan_name — review fix iteration $i" "$plan_work_dir/review_input_$((i + 1)).tsv" "$plan_work_dir"

    review_rounds_completed=$i
    echo "reviews_done=$i" > "$plan_work_dir/state"
  done

  log "========================================="
  log "Plan '$plan_name' completed after review-fix loop."
  log "========================================="

  mkdir -p "$PLAN_DIR/done"
  local done_name="${plan_name%.md}_$(date '+%Y%m%d_%H%M%S').done.md"
  local done_path="$PLAN_DIR/done/$done_name"

  {
    cat "$plan_file"
    echo ""
    echo "---"
    echo ""
    echo "## Execution Summary"
    echo ""
    echo "- **Completed:** $(date '+%Y-%m-%d %H:%M:%S')"
    echo "- **Review rounds:** $review_rounds_completed / $REVIEW_ITERATIONS"
    echo "- **Repos changed:**"
    if [ -f "$plan_work_dir/commits.tsv" ]; then
      while IFS=$'\t' read -r repo_name hash; do
        echo "  - \`$repo_name\` — commit \`$hash\`"
      done < "$plan_work_dir/commits.tsv"
    else
      echo "  - (none)"
    fi
    echo ""
    for r in $(seq 1 $review_rounds_completed); do
      echo "### Review Round $r"
      echo ""
      if [ -f "$plan_work_dir/combined_review_$r.txt" ]; then
        cat "$plan_work_dir/combined_review_$r.txt"
      else
        echo "(no review data)"
      fi
      echo ""
    done
  } > "$done_path"

  rm "$plan_file"
  rm -f "$plan_work_dir/pending"
  rm -f "$plan_work_dir/state"
  log "Wrote done file: $done_name"
}

execute_plan() {
  local plan_file="$1"
  local plan_name="$(basename "$plan_file")"

  log "========================================="
  log "Found plan: $plan_name"
  log "========================================="

  local plan_content
  plan_content="$(cat "$plan_file")"

  local work_id="$(date '+%Y%m%d_%H%M%S')_${plan_name%.md}"
  local plan_work_dir="$WORK_DIR/$work_id"
  mkdir -p "$plan_work_dir"

  log "[Step 1] Executing plan via Claude Code..."
  cd "$PROJECT_DIR"

  retry_on_limit "Plan execution" claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 "$plan_content"
  local exit_code=$?
  local exec_output="$RETRY_OUTPUT"
  echo "$exec_output" >> "$LOG_FILE"
  echo "$exec_output" > "$plan_work_dir/execution.log"

  if [ $exit_code -ne 0 ]; then
    log "Plan execution failed with exit code $exit_code. Will retry next cycle."
    return 1
  fi

  log "Plan execution completed. Committing changes..."
  commit_all_repos "$plan_name — execution" "$plan_work_dir/review_input_1.tsv" "$plan_work_dir"

  echo "$plan_file" > "$plan_work_dir/pending"

  run_review_loop "$plan_file" "$plan_name" "$plan_work_dir"
}

# Fix #1: Explicit return values instead of $resumed command execution
resume_pending_reviews() {
  local resumed=false
  for pending_file in "$WORK_DIR"/*/pending; do
    [ -f "$pending_file" ] || continue
    local plan_work_dir="$(dirname "$pending_file")"
    local plan_file
    plan_file="$(cat "$pending_file")"
    local plan_name="$(basename "$plan_file")"

    if [ ! -f "$plan_file" ]; then
      log "Pending review for '$plan_name' but plan file is gone. Cleaning up."
      rm -f "$pending_file"
      continue
    fi

    log "Resuming pending review for: $plan_name"
    run_review_loop "$plan_file" "$plan_name" "$plan_work_dir"
    resumed=true
  done
  if $resumed; then return 0; else return 1; fi
}

check_plans() {
  log "Checking for plans in $PLAN_DIR..."

  if resume_pending_reviews; then
    log "Resumed pending reviews. Will check for new plans next cycle."
    return
  fi

  local found=0
  while IFS= read -r -d '' plan_file; do
    [ -f "$plan_file" ] || continue
    found=1
    if ! execute_plan "$plan_file"; then
      log "Plan failed — will retry next cycle. Stopping current cycle."
      return
    fi
  done < <(find "$PLAN_DIR" -maxdepth 1 -name "*.md" -print0 2>/dev/null | sort -z)

  if [ $found -eq 0 ]; then
    log "No plans found."
  fi
}

# --- Main ---
acquire_lock

log "Dev Agent started. Checking every 10 minutes."
log "Plan directory: $PLAN_DIR"
log "Project directory: $PROJECT_DIR"
log "Review iterations: $REVIEW_ITERATIONS"

while true; do
  check_plans
  log "Sleeping 10 minutes..."
  sleep 600
done
