"""All git operations owl performs, behind one object per repo.

``GitClient`` wraps ``git -C <repo_root> ...``. Every git call owl makes goes
through a method here, which gives the test suite a single seam to fake.
The state-machine and plan-runner code depend on the ``GitLike`` protocol,
not on ``GitClient`` directly, so tests can pass an in-memory ``FakeGit``
(see ``tests/conftest.py``) without spawning real git.

Behavioral parity between ``GitClient`` and ``FakeGit`` is verified by
``tests/integration/test_git_contract.py``, which runs the same operation
sequence against a real tmp repo and the fake.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GitResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


@runtime_checkable
class GitLike(Protocol):
    """The subset of git operations owl needs. Implemented by GitClient and FakeGit."""

    repo_root: Path

    def is_inside_work_tree(self) -> bool: ...
    def has_commits(self) -> bool: ...
    def has_local_changes(self) -> bool: ...
    def current_branch(self) -> str: ...
    def head_hash(self) -> str | None: ...
    def short_head(self) -> str | None: ...
    def verify_ref(self, ref: str) -> bool: ...
    def default_branch(self) -> str: ...
    def fetch(self, remote: str, ref: str) -> bool: ...
    def checkout(self, ref: str, *, detach: bool = False, create: bool = False) -> bool: ...
    def branch_delete(self, name: str, *, force: bool = False) -> bool: ...
    def add_all(self) -> bool: ...
    def commit(self, message: str) -> bool: ...
    def diff_name_only(self, ref: str = "HEAD") -> list[str]: ...
    def untracked_files(self) -> list[str]: ...
    def reset_hard(self, ref: str = "HEAD") -> bool: ...
    def clean(self) -> bool: ...
    def stash_push(self, message: str) -> bool: ...
    def stash_list_contains(self, message: str) -> bool: ...
    def stash_apply(self, message: str) -> bool: ...
    def stash_drop(self, message: str) -> bool: ...
    def push(self, remote: str, branch: str, *, set_upstream: bool = True) -> bool: ...
    def log_oneline(self, rev_range: str) -> list[str]: ...
    def update_ref_delete(self, ref: str) -> bool: ...
    def worktree_add(self, path: Path, ref: str = "HEAD", *, detach: bool = True) -> bool: ...
    def worktree_remove(self, path: Path, *, force: bool = True) -> bool: ...
    def worktree_prune(self) -> bool: ...


class GitClient:
    """Subprocess-backed implementation of ``GitLike``."""

    def __init__(self, repo_root: Path, *, runner=None):
        self.repo_root = Path(repo_root)
        self._runner = runner or _subprocess_runner

    # ── low-level ────────────────────────────────────────────────────────────

    def run(self, *args: str) -> GitResult:
        return self._runner(["git", "-C", str(self.repo_root), *args])

    # ── inspection ─────────────────────────────────────────────────────────

    def is_inside_work_tree(self) -> bool:
        return self.run("rev-parse", "--is-inside-work-tree").ok

    def has_commits(self) -> bool:
        return self.run("rev-parse", "--verify", "HEAD").ok

    def has_local_changes(self) -> bool:
        """Tracked-modified, staged, OR untracked-not-ignored — any of these."""
        if not self.run("diff", "--quiet").ok:
            return True
        if not self.run("diff", "--cached", "--quiet").ok:
            return True
        return bool(self.untracked_files())

    def current_branch(self) -> str:
        return self.run("branch", "--show-current").stdout.strip()

    def head_hash(self) -> str | None:
        r = self.run("rev-parse", "HEAD")
        return r.stdout.strip() if r.ok else None

    def short_head(self) -> str | None:
        r = self.run("rev-parse", "--short", "HEAD")
        return r.stdout.strip() if r.ok else None

    def verify_ref(self, ref: str) -> bool:
        return self.run("rev-parse", "--verify", ref).ok

    def default_branch(self) -> str:
        r = self.run("symbolic-ref", "refs/remotes/origin/HEAD")
        if r.ok and r.stdout.strip():
            return r.stdout.strip().rsplit("/", 1)[-1]
        if self.verify_ref("refs/remotes/origin/main"):
            return "main"
        if self.verify_ref("refs/remotes/origin/master"):
            return "master"
        return "main"

    def diff_name_only(self, ref: str = "HEAD") -> list[str]:
        r = self.run("diff", "--name-only", ref)
        return [line for line in r.stdout.splitlines() if line]

    def untracked_files(self) -> list[str]:
        r = self.run("ls-files", "--others", "--exclude-standard")
        return [line for line in r.stdout.splitlines() if line]

    def log_oneline(self, rev_range: str) -> list[str]:
        r = self.run("log", rev_range, "--oneline")
        return [line for line in r.stdout.splitlines() if line]

    # ── mutation ─────────────────────────────────────────────────────────────

    def fetch(self, remote: str, ref: str) -> bool:
        return self.run("fetch", remote, ref).ok

    def checkout(self, ref: str, *, detach: bool = False, create: bool = False) -> bool:
        args = ["checkout"]
        if create:
            args.append("-b")
        if detach:
            args.append("--detach")
        args.append(ref)
        return self.run(*args).ok

    def branch_delete(self, name: str, *, force: bool = False) -> bool:
        return self.run("branch", "-D" if force else "-d", name).ok

    def add_all(self) -> bool:
        return self.run("add", "-A").ok

    def commit(self, message: str) -> bool:
        """Return True on a successful commit, False if there was nothing to commit."""
        r = self.run("commit", "-m", message)
        if r.ok:
            return True
        if "nothing to commit" in (r.stdout + r.stderr).lower():
            return False
        # A genuine failure (e.g. hook rejected). Surface as False; the caller
        # inspects has_local_changes / head movement to decide what to do.
        return False

    def reset_hard(self, ref: str = "HEAD") -> bool:
        return self.run("reset", "--hard", ref).ok

    def clean(self) -> bool:
        return self.run("clean", "-fd").ok

    def stash_push(self, message: str) -> bool:
        return self.run("stash", "push", "-u", "-m", message).ok

    def stash_list_contains(self, message: str) -> bool:
        r = self.run("stash", "list")
        return any(message in line for line in r.stdout.splitlines())

    def stash_apply(self, message: str) -> bool:
        return self.run("stash", "apply", f"stash^{{/{message}}}").ok

    def stash_drop(self, message: str) -> bool:
        return self.run("stash", "drop", f"stash^{{/{message}}}").ok

    def push(self, remote: str, branch: str, *, set_upstream: bool = True) -> bool:
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args += [remote, branch]
        return self.run(*args).ok

    def update_ref_delete(self, ref: str) -> bool:
        return self.run("update-ref", "-d", ref).ok

    # ── worktrees ────────────────────────────────────────────────────────────

    def worktree_add(self, path: Path, ref: str = "HEAD", *, detach: bool = True) -> bool:
        args = ["worktree", "add"]
        if detach:
            args.append("--detach")
        args += [str(path), ref]
        return self.run(*args).ok

    def worktree_remove(self, path: Path, *, force: bool = True) -> bool:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        return self.run(*args).ok

    def worktree_prune(self) -> bool:
        return self.run("worktree", "prune").ok


def _subprocess_runner(argv: list[str]) -> GitResult:
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return GitResult(rc=127, stdout="", stderr="git not found")
    return GitResult(rc=result.returncode, stdout=result.stdout, stderr=result.stderr)
