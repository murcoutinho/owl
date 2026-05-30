"""Pre-queue linter for Owl plan files.

Enforces two rules from the owl-plan-author skill:

1. **Edit-target path rule.** Sections titled "What to change" and
   "Files to modify" must reference files by repo-relative path —
   never by absolute source path, never by the ``<project-root>/``
   placeholder. Violations cause the silent "Plan produced no changes"
   failure because the agent edits the source repo instead of the
   per-plan worktree.

2. **No-plan-references sentinel.** Plan files are deleted from the
   queue once Owl finishes them, so any "plan N" reference in shipped
   code becomes a dangling pointer. Plans must include the literal
   sentinel "No plan-number references in code" somewhere in the body
   so the implementation agent receives the directive.

The linter is a standalone author tool. It is not invoked by Owl's
runtime — the runtime worktree contract is an independent agent-facing
safeguard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SENTINEL_PATTERN = re.compile(r"no plan-number references in code", re.IGNORECASE)

_EDIT_HEADING_PATTERN = re.compile(
    r"^\s*##\s+.*(what to change|files to modify)", re.IGNORECASE
)
_ANY_H2_PATTERN = re.compile(r"^\s*##\s+")
_FRONTMATTER_FENCE = re.compile(r"^---\s*$")
_CODE_FENCE = re.compile(r"^\s*```")
_PROJECT_ROOT_PLACEHOLDER = "<project-root>/"

# Match an absolute path token: leading "/" followed by a path-like body.
# Allowed leading contexts: start of line, whitespace, or one of the
# punctuation characters that commonly precede a path in prose / lists.
_ABS_PATH_PATTERN = re.compile(
    r"(?:^|[\s(\[<>*_\-])"  # leading context (not captured)
    r"(/[A-Za-z0-9_.\-][^\s,)\"'|]*)"
)


@dataclass(frozen=True)
class Violation:
    line_no: int
    offender: str
    line: str


@dataclass(frozen=True)
class LintReport:
    path: Path
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    sentinel_missing: bool = False

    @property
    def ok(self) -> bool:
        return not self.violations and not self.sentinel_missing


def lint_plan(path: Path) -> LintReport:
    """Lint a plan file and return a structured report. Pure function."""
    text = path.read_text()
    sentinel_missing = SENTINEL_PATTERN.search(text) is None
    violations = tuple(_scan_edit_target_violations(text))
    return LintReport(
        path=path,
        violations=violations,
        sentinel_missing=sentinel_missing,
    )


def _scan_edit_target_violations(text: str):
    in_frontmatter = False
    saw_frontmatter_open = False
    in_edit_section = False
    in_code_fence = False

    for idx, raw_line in enumerate(text.splitlines(), start=1):
        # Strip a YAML frontmatter block at the very top of the file.
        if idx == 1 and _FRONTMATTER_FENCE.match(raw_line):
            in_frontmatter = True
            saw_frontmatter_open = True
            continue
        if in_frontmatter:
            if _FRONTMATTER_FENCE.match(raw_line):
                in_frontmatter = False
            continue

        if _CODE_FENCE.match(raw_line):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        if _ANY_H2_PATTERN.match(raw_line):
            in_edit_section = bool(_EDIT_HEADING_PATTERN.match(raw_line))
            continue

        if not in_edit_section:
            continue

        # Strip inline-code backticks so `/abs/path` is caught like /abs/path.
        scrubbed = raw_line.replace("`", "")

        if _PROJECT_ROOT_PLACEHOLDER in scrubbed:
            yield Violation(idx, _PROJECT_ROOT_PLACEHOLDER, raw_line)
            continue

        m = _ABS_PATH_PATTERN.search(scrubbed)
        if m:
            yield Violation(idx, m.group(1), raw_line)

    # `saw_frontmatter_open` is intentional: if a plan opens "---" but never
    # closes it, we treat the rest as frontmatter and report no edit-target
    # violations — that case is malformed enough that validate will catch it.
    _ = saw_frontmatter_open


def format_report(report: LintReport) -> str:
    """Human-readable rendering. Matches the bash linter's exit-message shape."""
    if report.ok:
        return f"lint_plan: {report.path} — OK (0 violations)"

    lines = [f"lint_plan: {report.path} — FAIL", ""]
    if report.violations:
        lines.append("Edit-target path violations (Sections 'What to change' / 'Files to modify'):")
        for v in report.violations:
            lines.append(f"  line {v.line_no}: {v.offender}")
            lines.append(f"    > {v.line}")
        lines.append("")
        lines.append(
            "Those sections must use repo-relative paths (e.g. 'pipeline/foo.py' "
            "or '<repo-name>/pipeline/foo.py'). Absolute source paths and the "
            "<project-root>/ placeholder belong only in Section 3 (anchors)."
        )
        lines.append("")
    if report.sentinel_missing:
        lines.append("Missing sentinel: 'No plan-number references in code'")
        lines.append("  > Plans must include this directive verbatim in section 5")
        lines.append("    ('What does NOT change'). It tells the implementation agent")
        lines.append("    not to mention plan numbers in shipped code, comments,")
        lines.append("    docstrings, commit messages, test names, or log messages.")
        lines.append("    Plan files are deleted after merge — every 'plan N' reference")
        lines.append("    in the codebase becomes a dangling pointer.")
        lines.append("")
        lines.append("    See SKILL.md Step 4 §5 for the required wording to copy in.")
    return "\n".join(lines)
