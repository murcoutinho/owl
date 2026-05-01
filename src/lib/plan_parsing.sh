#!/bin/bash
#
# Plan frontmatter / parsing helpers.
#
# Sourced by src/owl.sh after REVIEW_ITERATIONS and MAX_REVIEW_ROUNDS are set.
# Do not source standalone — depends on owl.sh's globals.

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
