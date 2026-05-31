"""Parse YAML-style frontmatter from plan files.

Owl plans look like:

    ---
    review-rounds: 2
    priority: low
    base-branch: owl/foo
    repo: saudade
    ---
    # actual plan body in markdown

Only four keys are recognized (anything else is ignored on read for
forward-compat). Both hyphen and underscore forms are accepted.

The parser is intentionally permissive on quotes and trailing whitespace —
the original bash implementation accepted both ``priority: low`` and
``priority: "low"``, and we preserve that. It is intentionally *strict* on
the ``repo:`` field: a missing or unknown repo is treated as an error by
the caller (``validate.check_plan``), because that is the bug class
(plan 286) that motivated this rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """Result of parsing a plan's YAML frontmatter.

    All fields have sensible defaults so the caller never has to guess what
    a missing key means. ``repo`` is the only field that can be ``None`` —
    that signals "missing", which the validator turns into a hard error.
    """

    review_rounds: int
    priority: Literal["normal", "low"]
    base_branch: str | None
    repo: str | None

    @property
    def is_low_priority(self) -> bool:
        return self.priority == "low"


# Keys we accept in either hyphen or underscore form.
_ALIASES = {
    "review-rounds": "review_rounds",
    "review_rounds": "review_rounds",
    "priority": "priority",
    "base-branch": "base_branch",
    "base_branch": "base_branch",
    "repo": "repo",
}


def _strip_quotes(value: str) -> str:
    """Strip surrounding ``"..."`` or ``'...'`` from a YAML scalar."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _extract_frontmatter_block(text: str) -> tuple[dict[str, str], str]:
    """Split text into (frontmatter dict, body).

    Returns an empty dict and the original text if there is no frontmatter
    fence. We follow the bash convention: the opening ``---`` must be on
    line 1 (no leading whitespace, may have trailing whitespace).
    """
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].rstrip() != "---":
        return {}, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end = i
            break
    if end is None:
        # Unclosed frontmatter — treat as no frontmatter, keep the body intact.
        return {}, text

    fm: dict[str, str] = {}
    for raw in lines[1:end]:
        # Comments or blank lines inside frontmatter are tolerated.
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in _ALIASES:
            fm[_ALIASES[key]] = _strip_quotes(value)

    body = "\n".join(lines[end + 1 :])
    # Preserve a single trailing newline if the original had one.
    if text.endswith("\n") and not body.endswith("\n"):
        body += "\n"
    return fm, body


def parse(
    text: str,
    *,
    default_review_rounds: int = 2,
    max_review_rounds: int = 3,
) -> Frontmatter:
    """Parse plan source text and return a Frontmatter.

    ``default_review_rounds`` and ``max_review_rounds`` mirror the bash
    constants. The parser clamps to ``max`` and falls back to ``default``
    when the field is missing or unparseable.
    """
    fields, _ = _extract_frontmatter_block(text)

    review_rounds = default_review_rounds
    raw_rr = fields.get("review_rounds")
    if raw_rr is not None:
        try:
            value = int(raw_rr)
            if value >= 1:
                review_rounds = min(value, max_review_rounds)
        except ValueError:
            pass

    raw_priority = (fields.get("priority") or "").lower()
    priority: Literal["normal", "low"] = "low" if raw_priority == "low" else "normal"

    base_branch = fields.get("base_branch") or None
    repo = fields.get("repo") or None
    return Frontmatter(
        review_rounds=review_rounds,
        priority=priority,
        base_branch=base_branch,
        repo=repo,
    )


def strip_frontmatter(text: str) -> str:
    """Return the plan body without its frontmatter block."""
    _, body = _extract_frontmatter_block(text)
    return body


def parse_file(
    path: Path,
    *,
    default_review_rounds: int = 2,
    max_review_rounds: int = 3,
) -> Frontmatter:
    """Convenience wrapper that reads ``path`` and calls ``parse``."""
    return parse(
        path.read_text(),
        default_review_rounds=default_review_rounds,
        max_review_rounds=max_review_rounds,
    )
