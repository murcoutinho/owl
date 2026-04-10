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
LOCK_FILE="$SCRIPT_DIR/../.agent.lock"
REVIEW_ITERATIONS=2
MAX_REVIEW_ROUNDS=3
RETRY_WAIT=600
MAX_RETRIES=50
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
# Only these repos will be managed by Owl. Set via OWL_TARGET_REPOS env var.
TARGET_REPOS="${OWL_TARGET_REPOS:-project-api project-web}"

# Low-priority plans are skipped when this is "1". Default: include everything.
# Controlled by env var OWL_SKIP_LOW_PRIORITY or the CLI flag --skip-low-priority.
# Use case: run during the day with --skip-low-priority to save tokens, then at
# night re-run without the flag to drain the low-priority queue.
SKIP_LOW_PRIORITY="${OWL_SKIP_LOW_PRIORITY:-0}"

# Parse CLI args (only --skip-low-priority and --help supported for now).
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
    -h|--help)
      cat <<'HELPEOF'
Usage: agent.sh [--skip-low-priority] [--include-low-priority]

  --skip-low-priority     Skip plans whose frontmatter has `priority: low`.
                          Also honored via env var OWL_SKIP_LOW_PRIORITY=1.
  --include-low-priority  Force-include low-priority plans even if the env
                          var is set (useful for a nightly drain run).
  -h, --help              Show this help.

All other configuration is via environment variables — see README.
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

# List .git dirs for target repos only (null-delimited, compatible with existing loops)
find_target_repos() {
  for repo_name in $TARGET_REPOS; do
    local git_dir="$PROJECT_DIR/$repo_name/.git"
    if [ -d "$git_dir" ]; then
      printf '%s\0' "$git_dir"
    fi
  done
}

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
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

  case "$provider" in
    claude)
      retry_on_limit "Claude run" bash -c 'claude --print --dangerously-skip-permissions --model "$1" - < "$2"' _ "$model" "$prompt_file"
      ;;
    codex)
      retry_on_limit "Codex run" bash -c 'codex exec --full-auto --skip-git-repo-check --model "$1" - < "$2"' _ "$model" "$prompt_file"
      ;;
    none)
      RETRY_OUTPUT=""
      return 0
      ;;
    *)
      log "Invalid provider '$provider'. Expected claude, codex, or none."
      return 1
      ;;
  esac
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
  echo "$output" | grep -qiE "rate.?limit|too many requests|429|overloaded|capacity|quota exceeded|try again"
}

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

# ─── Step 2: Reset all repos to the base branch & pull ───
# If base_branch is empty, resets to main (default). Otherwise, for each repo:
#   - if origin has base_branch, fetch and check it out
#   - if origin does not have base_branch, fall back to main for that repo
#     (the dependency is considered satisfied — most likely the base plan was
#     already merged and its branch deleted)
reset_all_repos_to_base() {
  local base_branch="$1"
  if [ -z "$base_branch" ]; then
    log "Resetting all repos to main and pulling..."
  else
    log "Resetting all repos to base branch '$base_branch' (fallback: main) and pulling..."
  fi

  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    local repo_name="$(basename "$repo_root")"

    # Skip repos with no commits
    if ! git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      continue
    fi

    # Skip repos with any local changes — tracked, staged, or untracked (protect user's work)
    if ! git -C "$repo_root" diff --quiet 2>/dev/null || \
       ! git -C "$repo_root" diff --cached --quiet 2>/dev/null || \
       [ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
      log "  $repo_name: SKIPPING — has local changes"
      continue
    fi

    # Decide which branch this repo should land on. Start from the requested
    # base; if origin does not have it, fall back to main for this repo.
    local target_branch="main"
    if [ -n "$base_branch" ]; then
      git -C "$repo_root" fetch origin "$base_branch" 2>/dev/null
      if git -C "$repo_root" rev-parse --verify "refs/remotes/origin/$base_branch" >/dev/null 2>&1; then
        target_branch="$base_branch"
        log "  $repo_name: using base branch '$base_branch' from origin"
      else
        log "  $repo_name: base branch '$base_branch' not on origin — falling back to main"
      fi
    fi

    local current_branch
    current_branch=$(git -C "$repo_root" branch --show-current 2>>"$LOG_FILE")

    if [ "$current_branch" != "$target_branch" ]; then
      log "  $repo_name: switching from '$current_branch' to '$target_branch'"
      # For a remote-only branch, create a local tracking branch on checkout.
      if git -C "$repo_root" rev-parse --verify "$target_branch" >/dev/null 2>&1; then
        git -C "$repo_root" checkout "$target_branch" 2>>"$LOG_FILE" || {
          log "  $repo_name: WARNING — failed to checkout '$target_branch'"
          continue
        }
      else
        git -C "$repo_root" checkout -b "$target_branch" --track "origin/$target_branch" 2>>"$LOG_FILE" || {
          log "  $repo_name: WARNING — failed to check out tracking branch for '$target_branch'"
          continue
        }
      fi
    fi

    # Pull latest (non-destructive, fast-forward only)
    git -C "$repo_root" pull --ff-only origin "$target_branch" 2>>"$LOG_FILE" || \
      log "  $repo_name: pull --ff-only failed (may need manual merge)"

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

  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    local repo_name="$(basename "$repo_root")"

    # Skip repos that don't have this branch
    if ! git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
      continue
    fi

    # Determine the PR base for this repo. Default to main; if the plan
    # declared a base branch AND origin has it, target that instead.
    local repo_pr_base="main"
    if [ -n "$pr_base_branch" ]; then
      git -C "$repo_root" fetch origin "$pr_base_branch" 2>/dev/null
      if git -C "$repo_root" rev-parse --verify "refs/remotes/origin/$pr_base_branch" >/dev/null 2>&1; then
        repo_pr_base="$pr_base_branch"
      fi
    fi

    # Skip if no commits on branch beyond the PR base
    if [ -z "$(git -C "$repo_root" log "origin/${repo_pr_base}..${branch_name}" --oneline 2>/dev/null)" ]; then
      git -C "$repo_root" branch -d "$branch_name" 2>/dev/null || true
      continue
    fi

    log "Pushing branch '$branch_name' in $repo_name (PR base: $repo_pr_base)..."
    if ! git -C "$repo_root" checkout "$branch_name" 2>>"$LOG_FILE"; then
      log "  $repo_name: failed to checkout branch. Skipping PR."
      continue
    fi
    if ! git -C "$repo_root" push -u origin "$branch_name" 2>>"$LOG_FILE"; then
      log "  $repo_name: push failed. Skipping PR."
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
Generated by [Owl](${OWL_REPO_URL:-https://github.com/YOUR_ORG/owl})
EOF
)" 2>&1) || true

    if echo "$pr_url" | grep -q "^https://"; then
      log "PR created in $repo_name: $pr_url"
      echo "$repo_name	$pr_url" >> "$plan_work_dir/pull_requests.tsv"
    else
      log "Failed to create PR in $repo_name: $pr_url"
    fi

  done < <(find_target_repos)
}

# ─── Step 8: Switch all repos back to main ───
switch_all_to_main() {
  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      git -C "$repo_root" checkout main 2>>"$LOG_FILE" || log "  $(basename "$repo_root"): WARNING — failed to checkout main"
    fi
  done < <(find_target_repos)
}

# ─── Execute plan ───
execute_plan() {
  local plan_file="$1"
  local plan_name="$(basename "$plan_file")"

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

  local work_id="$(date '+%Y%m%d_%H%M%S')_${plan_name%.md}"
  local plan_work_dir="$WORK_DIR/$work_id"
  mkdir -p "$plan_work_dir"
  echo "$plan_review_iterations" > "$plan_work_dir/review_iterations"
  # Persist the base branch so resume paths (run_review_loop, push_and_open_prs)
  # can recover it without re-parsing the plan file.
  if [ -n "$plan_base_branch" ]; then
    echo "$plan_base_branch" > "$plan_work_dir/base_branch"
  fi

  local branch_name="owl/${plan_name%.md}"

  # ── Step 2: Reset to base branch (or main) & pull ──
  reset_all_repos_to_base "$plan_base_branch"

  # ── Snapshot which repos are already dirty (not ours to clean) ──
  local pre_dirty_file="$plan_work_dir/pre_dirty_repos.txt"
  : > "$pre_dirty_file"
  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    if ! git -C "$repo_root" diff --quiet 2>/dev/null || \
       ! git -C "$repo_root" diff --cached --quiet 2>/dev/null || \
       [ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
      echo "$repo_root" >> "$pre_dirty_file"
    fi
  done < <(find_target_repos)

  if [ -s "$pre_dirty_file" ]; then
    log "Plan execution aborted before $IMPL_PROVIDER: repos have pre-existing local changes."
    while IFS= read -r repo_root; do
      [ -n "$repo_root" ] || continue
      log "  dirty repo: $(basename "$repo_root") ($repo_root)"
    done < "$pre_dirty_file"
    log "Clean or commit those repos first, then retry the plan."
    return 1
  fi

  # ── Step 3: Execute plan ──
  log "[Step 3] Executing plan via $IMPL_PROVIDER ($IMPL_MODEL)..."
  cd "$PROJECT_DIR"

  local plan_prompt_file="$plan_work_dir/plan_prompt.txt"
  cat > "$plan_prompt_file" <<PLANEOF
$plan_content

IMPORTANT INSTRUCTIONS:
- Do NOT commit, push, or create branches. Just write the code changes.
- The agent handles all git operations.
PLANEOF

  run_llm "$IMPL_PROVIDER" "$IMPL_MODEL" "$plan_prompt_file"
  local exit_code=$?
  local exec_output="$RETRY_OUTPUT"
  echo "$exec_output" >> "$LOG_FILE"
  echo "$exec_output" > "$plan_work_dir/execution.log"

  if [ $exit_code -ne 0 ] || [ -z "$exec_output" ] || echo "$exec_output" | grep -qi "^Execution error$"; then
    log "Plan execution failed (exit=$exit_code, output_len=${#exec_output}). Will retry next cycle."
    # Discard partial changes only in repos dirtied by THIS run (not pre-existing dirty repos)
    while IFS= read -r -d '' repo_dir; do
      local repo_root="$(dirname "$repo_dir")"
      local repo_name="$(basename "$repo_root")"
      # Skip repos that were already dirty before execution
      if grep -qxF "$repo_root" "$pre_dirty_file" 2>/dev/null; then
        continue
      fi
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
  while IFS= read -r -d '' repo_dir; do
    local repo_root="$(dirname "$repo_dir")"
    local repo_name="$(basename "$repo_root")"

    # Skip repos with no changes
    if git -C "$repo_root" diff --quiet 2>/dev/null && \
       git -C "$repo_root" diff --cached --quiet 2>/dev/null && \
       [ -z "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
      continue
    fi

    # Skip repos that were already dirty before execution (not our changes)
    if grep -qxF "$repo_root" "$pre_dirty_file" 2>/dev/null; then
      log "  $repo_name: SKIPPING — had pre-existing local changes"
      continue
    fi

    # Capture the base commit (whatever was checked out before this plan ran —
    # either main or the plan's declared base_branch). The reviewer uses this
    # as the left side of the diff range.
    local base_hash="NONE"
    if git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
      base_hash=$(git -C "$repo_root" rev-parse HEAD)
    fi

    log "  $repo_name: changes detected → creating branch '$branch_name'"

    # Create and switch to branch
    # Create new branch; if it already exists, delete it first (stale from a prior failed run)
    if git -C "$repo_root" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
      log "  $repo_name: deleting stale branch '$branch_name'"
      git -C "$repo_root" branch -D "$branch_name" 2>>"$LOG_FILE"
    fi
    git -C "$repo_root" checkout -b "$branch_name" 2>>"$LOG_FILE"

    # Stage and commit
    git -C "$repo_root" add -A 2>>"$LOG_FILE"
    git -C "$repo_root" commit -m "[owl] ${plan_name%.md} — execution" 2>>"$LOG_FILE"

    local after_hash
    after_hash=$(git -C "$repo_root" rev-parse HEAD)
    local short_hash
    short_hash=$(git -C "$repo_root" rev-parse --short HEAD)

    log "  $repo_name: committed ($short_hash)"

    printf '%s\t%s\t%s\t%s\n' "$repo_name" "$repo_root" "$base_hash" "$after_hash" >> "$plan_work_dir/review_input_1.tsv"
    printf '%s\t%s\n' "$repo_name" "$short_hash" >> "$plan_work_dir/commits.tsv"

  done < <(find_target_repos)

  # ── Check that something was actually committed ──
  if [ ! -s "$plan_work_dir/review_input_1.tsv" ]; then
    log "Plan produced no changes in any repo. Failing — will retry next cycle."
    return 1
  fi

  # ── Step 5: Mark pending ──
  echo "$plan_file" > "$plan_work_dir/pending"
  echo "$branch_name" > "$plan_work_dir/branch"

  # ── Step 6: Review loop ──
  run_review_loop "$plan_file" "$plan_name" "$plan_work_dir"
}

# ─── Review loop ───
run_review_loop() {
  local plan_file="$1"
  local plan_name="$2"
  local plan_work_dir="$3"

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
    while IFS= read -r -d '' repo_dir; do
      local repo_root="$(dirname "$repo_dir")"
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

  local review_rounds_completed=$reviews_done
  local reviews_skipped=0

  for i in $(seq $((reviews_done + 1)) $review_iterations); do
    log "-----------------------------------------"
    log "[Iteration $i/$review_iterations] Review phase"
    log "-----------------------------------------"

    local manifest="$plan_work_dir/review_input_$i.tsv"

    if [ ! -s "$manifest" ]; then
      log "No manifest for round $i. Skipping."
      reviews_skipped=$((reviews_skipped + 1))
      continue
    fi

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
    [ "$(normalize_provider "$REVIEWER1_PROVIDER")" = "none" ] && reviewer1_enabled=false
    [ "$(normalize_provider "$REVIEWER2_PROVIDER")" = "none" ] && reviewer2_enabled=false

    if [ "$REVIEW_MODE" = "parallel" ]; then
      log "Spawning reviewers in parallel..."

      local reviewer1_pid=""
      local reviewer2_pid=""

      if $reviewer1_enabled; then
        (
          rc=0
          run_llm "$REVIEWER1_PROVIDER" "$REVIEWER1_MODEL" "$review_prompt_file" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer1_file" 2>&1 &
        reviewer1_pid=$!
      else
        echo "LGTM" > "$reviewer1_file"
      fi

      if $reviewer2_enabled; then
        (
          rc=0
          run_llm "$REVIEWER2_PROVIDER" "$REVIEWER2_MODEL" "$review_prompt_file" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer2_file" 2>&1 &
        reviewer2_pid=$!
      else
        echo "LGTM" > "$reviewer2_file"
      fi

      if [ -n "$reviewer1_pid" ]; then
        wait "$reviewer1_pid" || reviewer1_ok=false
      fi
      log "$REVIEWER1_LABEL review complete (enabled=$reviewer1_enabled success=$reviewer1_ok)."

      if [ -n "$reviewer2_pid" ]; then
        wait "$reviewer2_pid" || reviewer2_ok=false
      fi
      log "$REVIEWER2_LABEL review complete (enabled=$reviewer2_enabled success=$reviewer2_ok)."

    else
      log "Running reviewers sequentially..."

      if $reviewer1_enabled; then
        log "Running $REVIEWER1_LABEL reviewer..."
        (
          rc=0
          run_llm "$REVIEWER1_PROVIDER" "$REVIEWER1_MODEL" "$review_prompt_file" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer1_file" 2>&1
        [ $? -eq 0 ] || reviewer1_ok=false
      else
        echo "LGTM" > "$reviewer1_file"
      fi
      log "$REVIEWER1_LABEL review complete (enabled=$reviewer1_enabled success=$reviewer1_ok)."

      if $reviewer2_enabled; then
        log "Running $REVIEWER2_LABEL reviewer..."
        (
          rc=0
          run_llm "$REVIEWER2_PROVIDER" "$REVIEWER2_MODEL" "$review_prompt_file" || rc=$?
          echo "$RETRY_OUTPUT"
          exit $rc
        ) > "$reviewer2_file" 2>&1
        [ $? -eq 0 ] || reviewer2_ok=false
      else
        echo "LGTM" > "$reviewer2_file"
      fi
      log "$REVIEWER2_LABEL review complete (enabled=$reviewer2_enabled success=$reviewer2_ok)."
    fi

    if ! $reviewer1_ok && ! $reviewer2_ok; then
      log "All enabled reviewers failed. Skipping fix phase for iteration $i."
      reviews_skipped=$((reviews_skipped + 1))
      continue
    fi

    local reviewer1_review=""
    local reviewer2_review=""
    [ -f "$reviewer1_file" ] && reviewer1_review=$(cat "$reviewer1_file")
    [ -f "$reviewer2_file" ] && reviewer2_review=$(cat "$reviewer2_file")

    local combined_review="## $REVIEWER1_LABEL Review

$reviewer1_review

## $REVIEWER2_LABEL Review

$reviewer2_review"

    echo "$combined_review" > "$plan_work_dir/combined_review_$i.txt"
    review_rounds_completed=$i

    local reviewer1_trimmed reviewer2_trimmed
    reviewer1_trimmed=$(echo "$reviewer1_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    reviewer2_trimmed=$(echo "$reviewer2_review" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if { ! $reviewer1_enabled || [ "$reviewer1_trimmed" = "LGTM" ]; } && \
       { ! $reviewer2_enabled || [ "$reviewer2_trimmed" = "LGTM" ]; }; then
      log "All enabled reviewers say LGTM. No fixes needed."
      break
    fi

    # ── Fix phase ──
    log "[Iteration $i/$review_iterations] Fix phase"

    local fix_prompt_file="$plan_work_dir/fix_prompt_$i.txt"
    cat > "$fix_prompt_file" <<FIXEOF
You received the following code review feedback on recent changes in this project. Apply the necessary fixes. Only change what the review asks for — do not refactor unrelated code.

CRITICAL: The plan below is the source of truth for what the code should do. If a reviewer comment contradicts the plan (e.g. asks you to revert a change that the plan explicitly requested), IGNORE that comment. Only apply fixes that are consistent with the plan. If a reviewer asks a hedged question ("if X is intentional, document it; if not, revert") and the plan confirms X is intentional, prefer a short comment or docstring over reverting.

IMPORTANT: Do NOT commit, push, or create branches. Just write the code fixes.

## Plan being implemented

${plan_content:-(plan file unavailable)}

## Review Feedback

$combined_review
FIXEOF

    log "Applying fixes via $FIX_PROVIDER ($FIX_MODEL)..."
    run_llm "$FIX_PROVIDER" "$FIX_MODEL" "$fix_prompt_file"
    echo "$RETRY_OUTPUT" | tee -a "$LOG_FILE" > "$plan_work_dir/fixes_$i.log"

    # Commit fixes in repos on the branch that have changes
    local next_manifest="$plan_work_dir/review_input_$((i + 1)).tsv"
    : > "$next_manifest"
    while IFS= read -r -d '' repo_dir; do
      local repo_root="$(dirname "$repo_dir")"
      local repo_name="$(basename "$repo_root")"
      local current_branch
      current_branch=$(git -C "$repo_root" branch --show-current 2>/dev/null)

      # Only commit in repos on the plan's branch with changes
      if [ "$current_branch" != "$branch_name" ]; then
        continue
      fi
      if git -C "$repo_root" diff --quiet 2>/dev/null && \
         git -C "$repo_root" diff --cached --quiet 2>/dev/null && \
         [ -z "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]; then
        continue
      fi

      local before_hash
      before_hash=$(git -C "$repo_root" rev-parse HEAD)

      git -C "$repo_root" add -A 2>/dev/null
      git -C "$repo_root" commit -m "[owl] ${plan_name%.md} — review fix iteration $i" 2>/dev/null

      local after_hash
      after_hash=$(git -C "$repo_root" rev-parse HEAD)
      local short_hash
      short_hash=$(git -C "$repo_root" rev-parse --short HEAD)

      log "  $repo_name: fix committed ($short_hash)"
      printf '%s\t%s\t%s\t%s\n' "$repo_name" "$repo_root" "$before_hash" "$after_hash" >> "$next_manifest"
      printf '%s\t%s\n' "$repo_name" "$short_hash" >> "$plan_work_dir/commits.tsv"
    done < <(find_target_repos)

    review_rounds_completed=$i
    echo "reviews_done=$i" > "$plan_work_dir/state"
  done

  # ── Step 7: Push and open PRs ──
  local reviews_successful=$(( review_rounds_completed > reviews_skipped ? review_rounds_completed - reviews_skipped : 0 ))
  if [ -n "$branch_name" ]; then
    log "[Step 7] Pushing branches and opening PRs..."
    push_and_open_prs "$branch_name" "$plan_name" "$plan_file" "$plan_work_dir" "$reviews_successful" "$review_iterations"
  fi

  # ── Step 8: Switch back to main ──
  log "[Step 8] Switching all repos back to main..."
  switch_all_to_main

  # ── Step 9: Write done file ──
  log "========================================="
  log "Plan '$plan_name' completed."
  log "========================================="

  mkdir -p "$PLAN_DIR/done"
  local done_name="${plan_name%.md}_$(date '+%Y%m%d_%H%M%S').done.md"
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
    for r in $(seq 1 $review_rounds_completed); do
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

  rm "$plan_file"
  rm -f "$plan_work_dir/pending"
  rm -f "$plan_work_dir/state"
  rm -f "$plan_work_dir/review_iterations"
  log "Wrote done file: $done_name"
}

# ─── Resume pending reviews ───
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

# ─── Main check loop ───
check_plans() {
  log "Checking for plans in $PLAN_DIR..."

  if resume_pending_reviews; then
    log "Resumed pending reviews. Will check for new plans next cycle."
    return
  fi

  local found=0
  local skipped_low=0
  while IFS= read -r -d '' plan_file; do
    [ -f "$plan_file" ] || continue

    # Honor low-priority skip: plans marked `priority: low` in frontmatter
    # are bypassed when SKIP_LOW_PRIORITY=1. Still count them as found so
    # the "No plans found" message does not fire when the queue only
    # contains skipped items.
    if [ "$SKIP_LOW_PRIORITY" = "1" ]; then
      local plan_priority
      plan_priority="$(parse_plan_priority "$plan_file")"
      if [ "$plan_priority" = "low" ]; then
        log "  skipping low-priority plan: $(basename "$plan_file") (SKIP_LOW_PRIORITY=1)"
        skipped_low=$((skipped_low + 1))
        continue
      fi
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

# ─── Main ───
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
