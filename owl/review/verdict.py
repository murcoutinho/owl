"""Extract the actionable verdict from a reviewer LLM's raw output.

Codex wraps its model output in a transcript that includes ``codex`` /
``user`` / ``tokens used`` boundary lines. We only want the last ``codex``
block — that's the verdict. Claude does not produce this transcript, so
for Claude we just trim and return the file as-is.

Mirrors ``extract_reviewer_verdict`` (owl.sh:380-393). The fallback to
"trimmed full text when no codex markers" is critical: a Claude reviewer
should be treated as having its entire stdout as the verdict, otherwise
we'd silently drop reviewer output.
"""

from __future__ import annotations

import re

# A line consisting solely of "codex" or "tokens used" (with optional
# surrounding whitespace) is a transcript boundary marker in the codex CLI.
_CODEX_BOUNDARY = re.compile(r"^codex\s*$", re.MULTILINE)
_TOKENS_USED = re.compile(r"^tokens used.*$", re.IGNORECASE | re.MULTILINE)


def extract_verdict(raw: str) -> str:
    """Return the actionable verdict text from a reviewer's raw stdout.

    Strategy:

    1. If the text has no ``^codex$`` markers, return the trimmed input.
       This is the Claude reviewer path.
    2. Otherwise: find the *last* ``^codex$`` marker. The verdict is the
       text between it and the next ``^tokens used$`` line (or EOF).
    """
    if not _CODEX_BOUNDARY.search(raw):
        return raw.strip()

    # Find the last occurrence by walking matches and keeping the latest.
    last_match = None
    for m in _CODEX_BOUNDARY.finditer(raw):
        last_match = m
    assert last_match is not None  # we know at least one exists

    after = raw[last_match.end() :]
    tokens_match = _TOKENS_USED.search(after)
    if tokens_match:
        verdict = after[: tokens_match.start()]
    else:
        verdict = after
    return verdict.strip()


def is_lgtm(verdict: str) -> bool:
    """Return True if the (trimmed) verdict is exactly ``LGTM``.

    The bash side checks ``[ "$(cat … | tr -d '[:space:]')" = "LGTM" ]``
    (owl.sh:2054). We are slightly stricter: we strip whitespace but do
    not flatten internal characters, since a "LGTM" buried in a paragraph
    is not a clean approval.
    """
    return verdict.strip().upper() == "LGTM"
