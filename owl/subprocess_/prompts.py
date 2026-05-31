"""Wrap a base prompt with the proof-of-life ack directive and worktree contract.

Ports ``prepend_ack_prompt``. The wrapped prompt:

1. Tells the agent to write ``alive`` to an ack file as its FIRST action.
2. (When a worktree root is given that differs from the project dir) injects
   a WORKTREE CONTRACT telling the agent to translate any absolute path under
   the source project dir to the worktree before writing — this is what keeps
   the coder from editing the real checkout when the plan references absolute
   paths.

With the required single-``repo:`` field, the worktree contains exactly one
sub-repo, so the contract lists just that one.
"""

from __future__ import annotations

from pathlib import Path

_ACK_HEADER = """\
PROOF-OF-LIFE REQUIREMENT

Your VERY FIRST action MUST be a tool call that writes the single word \
'alive' to the absolute path below. Do this BEFORE reading any diff, running \
any git command, or thinking about the task. The harness cannot see your \
stdout while you are running — only files on disk — so this is how we confirm \
you are alive.

ACK_FILE_PATH: {ack_path}

After writing the ack file, proceed with the rest of the task below.

---
"""


def _worktree_contract(worktree_root: Path, project_dir: Path, sub_repos: list[str]) -> str:
    repo_lines = "\n".join(f"  {worktree_root / r}" for r in sub_repos)
    return f"""\
WORKTREE CONTRACT

You are working in an isolated git worktree:
  {worktree_root}

Sub-repos inside this worktree:
{repo_lines}

The plan below may reference files by absolute path under the source project \
directory:
  {project_dir}

Those absolute paths are IDENTIFIERS, not edit targets. Before calling \
Edit/Write/MultiEdit or any Bash command that writes files, translate every \
such path by replacing the prefix
  {project_dir}/<repo>/
with
  {worktree_root}/<repo>/

DO NOT create, modify, or delete any file outside {worktree_root}. Reading \
files outside the worktree for reference is fine; writing is not.

---
"""


def build_wrapped_prompt(
    *,
    ack_path: Path,
    base_prompt: str,
    worktree_root: Path | None = None,
    project_dir: Path | None = None,
    sub_repos: list[str] | None = None,
) -> str:
    """Return the wrapped prompt text. Pure — the caller writes it to disk."""
    parts = [_ACK_HEADER.format(ack_path=ack_path), ""]
    if (
        worktree_root is not None
        and project_dir is not None
        and worktree_root != project_dir
    ):
        parts.append(_worktree_contract(worktree_root, project_dir, sub_repos or []))
        parts.append("")
    parts.append(base_prompt)
    return "\n".join(parts)


def write_wrapped_prompt(
    out_path: Path,
    *,
    ack_path: Path,
    base_prompt: str,
    worktree_root: Path | None = None,
    project_dir: Path | None = None,
    sub_repos: list[str] | None = None,
) -> Path:
    """Build the wrapped prompt and write it to ``out_path``. Clears the ack file."""
    ack_path.unlink(missing_ok=True)
    text = build_wrapped_prompt(
        ack_path=ack_path,
        base_prompt=base_prompt,
        worktree_root=worktree_root,
        project_dir=project_dir,
        sub_repos=sub_repos,
    )
    out_path.write_text(text)
    return out_path
