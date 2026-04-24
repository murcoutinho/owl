#!/usr/bin/env bash
# Pre-queue linter for Owl plan files.
#
# Enforces the edit-target path rule from the owl-plan-author skill:
# Sections titled "What to change" and "Files to modify" must reference
# files by repo-relative path — never by absolute source path, never by
# the placeholder `<project-root>/...`. Violations commonly cause the
# "Plan produced no changes in any repo" silent-leak failure, because
# the agent edits the source repo instead of Owl's per-plan worktree.
#
# This script is deliberately standalone. It does NOT share code with
# src/owl.sh — Owl's runtime never runs this lint. Authors invoke it
# explicitly before queueing.
#
# Usage:
#   ./src/lint_plan.sh path/to/draft-plan.md
#
# Exit codes:
#   0  — plan passes the lint
#   1  — plan has violations (details printed to stdout)
#   2  — usage error (e.g. missing file)

set -uo pipefail

usage() {
  cat <<USAGE
Usage: $0 <plan-file>

Lints a draft Owl plan file for edit-target path violations.

Scans only Sections titled "What to change" and "Files to modify".
Flags any line in those sections that contains:
  - an absolute filesystem path (starts with /, excluding code fences
    and snippet contents), or
  - the placeholder \`<project-root>/\` (which is a doc marker, not an
    edit target).

Section 3 ("Existing files to anchor on") and every other section are
exempt — anchors may use absolute paths because they are read-only
grounding.
USAGE
}

if [ $# -ne 1 ]; then
  usage >&2
  exit 2
fi

plan_file="$1"

if [ ! -f "$plan_file" ]; then
  echo "lint_plan: file not found: $plan_file" >&2
  exit 2
fi

violations=$(awk '
  function is_heading(line) {
    return line ~ /^[[:space:]]*##[[:space:]]+/
  }

  BEGIN {
    in_frontmatter = 0
    in_edit_targets = 0
    in_code_fence = 0
  }

  # Skip YAML frontmatter (leading --- / --- block).
  NR == 1 && $0 ~ /^---[[:space:]]*$/ {
    in_frontmatter = 1
    next
  }
  in_frontmatter && $0 ~ /^---[[:space:]]*$/ {
    in_frontmatter = 0
    next
  }
  in_frontmatter { next }

  # Toggle code-fence state so we do not flag snippet contents.
  /^[[:space:]]*```/ {
    in_code_fence = !in_code_fence
    next
  }
  in_code_fence { next }

  is_heading($0) {
    heading = tolower($0)
    in_edit_targets = (heading ~ /what to change/ || heading ~ /files to modify/)
    next
  }

  in_edit_targets {
    line = $0
    # Strip inline-code backticks so `/abs/path` inside prose is caught
    # the same way as a bare /abs/path.
    gsub(/`/, "", line)

    # Rule 1: placeholder root.
    if (index(line, "<project-root>/") > 0) {
      printf "%d\t<project-root>/\t%s\n", NR, $0
      next
    }

    # Rule 2: any absolute path. Match " /xxx", "(/xxx", "[/xxx", "</xxx", or "/xxx" at start.
    # Tolerates commas, closing delimiters.
    if (match(line, /(^|[[:space:]([<>*_\-])\/[A-Za-z0-9_.-][^[:space:],)"'"'"'|]*/) > 0) {
      matched = substr(line, RSTART, RLENGTH)
      # Trim leading separator character.
      sub(/^[[:space:]([<>*_\-]/, "", matched)
      printf "%d\t%s\t%s\n", NR, matched, $0
    }
  }
' "$plan_file")

if [ -z "$violations" ]; then
  echo "lint_plan: $plan_file — OK (0 edit-target path violations)"
  exit 0
fi

echo "lint_plan: $plan_file — FAIL"
echo ""
echo "Edit-target path violations (Sections 'What to change' / 'Files to modify'):"
while IFS=$'\t' read -r line_no offender full_line; do
  [ -n "$line_no" ] || continue
  echo "  line $line_no: $offender"
  echo "    > $full_line"
done <<< "$violations"
echo ""
echo "Those sections must use repo-relative paths (e.g. 'pipeline/foo.py'"
echo "or '<repo-name>/pipeline/foo.py'). Absolute source paths and the"
echo "<project-root>/ placeholder belong only in Section 3 (anchors)."
exit 1
