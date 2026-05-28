"""Shared test fixtures and fakes.

The two big fakes — ``FakeGit`` and ``FakeLLM`` — let the state-machine
tests run without spawning real git, claude, or codex. The integration
tests use the real ``GitClient`` against a tmp repo instead (see the
``real_repo`` fixture).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from owl.git_ops import GitClient

# ─────────────────────────────────────────────────────────────────────────────
# FakeGit: in-memory implementation of the GitLike protocol
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FakeRepoState:
    branches: set[str] = field(default_factory=lambda: {"main"})
    current: str = "main"
    head: str | None = "hash0000"
    dirty: bool = False
    commit_should_fail: bool = False
    remote_refs: set[str] = field(default_factory=set)
    untracked: list[str] = field(default_factory=list)
    tracked_modified: list[str] = field(default_factory=list)
    _counter: int = 0


class FakeGitUniverse:
    """Holds per-path repo state and hands out FakeGit views.

    A 'universe' lets a worktree created from a source repo be resolvable by
    the same universe, mimicking ``git worktree add``.
    """

    def __init__(self) -> None:
        self.repos: dict[str, FakeRepoState] = {}
        self.calls: list[tuple[str, ...]] = []

    def add_repo(self, root: Path, **kwargs) -> FakeRepoState:
        state = FakeRepoState(**kwargs)
        self.repos[str(root)] = state
        return state

    def client(self, root: Path) -> FakeGit:
        return FakeGit(self, Path(root))

    def state(self, root: Path) -> FakeRepoState:
        return self.repos[str(root)]


class FakeGit:
    """In-memory GitLike. Drives the state machine in unit tests."""

    def __init__(self, universe: FakeGitUniverse, repo_root: Path) -> None:
        self._u = universe
        self.repo_root = repo_root
        # Auto-register unknown repos as empty so .client() on a fresh path works.
        if str(repo_root) not in universe.repos:
            universe.repos[str(repo_root)] = FakeRepoState(branches=set(), head=None)

    @property
    def _s(self) -> FakeRepoState:
        return self._u.repos[str(self.repo_root)]

    def _record(self, *args: str) -> None:
        self._u.calls.append((str(self.repo_root), *args))

    # ── inspection ───────────────────────────────────────────────────────────

    def is_inside_work_tree(self) -> bool:
        return str(self.repo_root) in self._u.repos

    def has_commits(self) -> bool:
        return self._s.head is not None

    def has_local_changes(self) -> bool:
        s = self._s
        return s.dirty or bool(s.untracked) or bool(s.tracked_modified)

    def current_branch(self) -> str:
        return self._s.current

    def head_hash(self) -> str | None:
        return self._s.head

    def short_head(self) -> str | None:
        h = self._s.head
        return h[:7] if h else None

    def verify_ref(self, ref: str) -> bool:
        s = self._s
        return ref in s.remote_refs or ref in s.branches or (ref in ("HEAD",) and s.head)

    def default_branch(self) -> str:
        s = self._s
        if "main" in s.branches or "refs/remotes/origin/main" in s.remote_refs:
            return "main"
        if "master" in s.branches or "refs/remotes/origin/master" in s.remote_refs:
            return "master"
        return "main"

    def diff_name_only(self, ref: str = "HEAD") -> list[str]:
        return list(self._s.tracked_modified)

    def untracked_files(self) -> list[str]:
        return list(self._s.untracked)

    def log_oneline(self, rev_range: str) -> list[str]:
        self._record("log", rev_range)
        return []

    # ── mutation ─────────────────────────────────────────────────────────────

    def fetch(self, remote: str, ref: str) -> bool:
        self._record("fetch", remote, ref)
        return f"refs/remotes/{remote}/{ref}" in self._s.remote_refs

    def checkout(self, ref: str, *, detach: bool = False, create: bool = False) -> bool:
        self._record("checkout", ref)
        s = self._s
        if create:
            s.branches.add(ref)
            s.current = ref
            return True
        if detach:
            s.current = ""  # detached HEAD has no branch name
            return True
        if ref in s.branches:
            s.current = ref
            return True
        return False

    def branch_delete(self, name: str, *, force: bool = False) -> bool:
        self._record("branch-delete", name)
        self._s.branches.discard(name)
        return True

    def add_all(self) -> bool:
        self._record("add")
        return True

    def commit(self, message: str) -> bool:
        self._record("commit", message)
        s = self._s
        if not self.has_local_changes():
            return False
        if s.commit_should_fail:
            return False
        s._counter += 1
        s.head = f"hash{s._counter:04d}"
        s.dirty = False
        s.untracked = []
        s.tracked_modified = []
        return True

    def reset_hard(self, ref: str = "HEAD") -> bool:
        self._record("reset-hard", ref)
        s = self._s
        s.dirty = False
        s.tracked_modified = []
        return True

    def clean(self) -> bool:
        self._record("clean")
        self._s.untracked = []
        return True

    def stash_push(self, message: str) -> bool:
        self._record("stash-push", message)
        s = self._s
        s.dirty = False
        s.untracked = []
        s.tracked_modified = []
        return True

    def stash_list_contains(self, message: str) -> bool:
        return True

    def stash_apply(self, message: str) -> bool:
        self._record("stash-apply", message)
        return True

    def stash_drop(self, message: str) -> bool:
        self._record("stash-drop", message)
        return True

    def push(self, remote: str, branch: str, *, set_upstream: bool = True) -> bool:
        self._record("push", remote, branch)
        return True

    def update_ref_delete(self, ref: str) -> bool:
        self._record("update-ref-delete", ref)
        self._s.remote_refs.discard(ref)
        return True

    def worktree_add(self, path: Path, ref: str = "HEAD", *, detach: bool = True) -> bool:
        self._record("worktree-add", str(path))
        # Mirror the source repo's branches/head into the new worktree path.
        src = self._s
        Path(path).mkdir(parents=True, exist_ok=True)
        self._u.repos[str(path)] = FakeRepoState(
            branches=set(src.branches),
            current="",  # detached
            head=src.head,
            remote_refs=set(src.remote_refs),
        )
        return True

    def worktree_remove(self, path: Path, *, force: bool = True) -> bool:
        self._record("worktree-remove", str(path))
        self._u.repos.pop(str(path), None)
        return True

    def worktree_prune(self) -> bool:
        return True


@pytest.fixture
def git_universe() -> FakeGitUniverse:
    return FakeGitUniverse()


# ─────────────────────────────────────────────────────────────────────────────
# Real git repo fixture (for integration + contract tests)
# ─────────────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def real_repo(tmp_path: Path) -> Path:
    """Initialise a real git repo with one commit and an 'origin' remote."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo


@pytest.fixture
def real_git(real_repo: Path) -> GitClient:
    return GitClient(real_repo)


# ─────────────────────────────────────────────────────────────────────────────
# FakeLLM (used by subprocess + integration tests)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LLMCall:
    provider: str
    model: str
    prompt: str
    session_id: str | None
    session_mode: str
    cwd: Path | None


class FakeLLM:
    """Records calls and replays scripted results.

    ``diff_writer`` lets the integration test simulate a coder that actually
    writes files into the worktree on the execution call.
    """

    def __init__(self) -> None:
        self.calls: list[LLMCall] = []
        self.script: list = []  # list[LLMResult] popped in order
        self.diff_writer = None  # Callable[[Path], None] | None
        self.cwd: Path | None = None

    def run(
        self,
        provider: str,
        model: str,
        prompt_path: Path,
        *,
        session_id: str | None = None,
        session_mode: str = "create",
        cwd: Path | None = None,
    ):
        from owl.subprocess_.llm import LLMResult

        prompt = Path(prompt_path).read_text() if Path(prompt_path).exists() else ""
        self.calls.append(
            LLMCall(provider, model, prompt, session_id, session_mode, cwd)
        )
        if self.diff_writer and cwd is not None:
            self.diff_writer(Path(cwd))
        if self.script:
            return self.script.pop(0)
        return LLMResult(rc=0, output="", elapsed_sec=1, timed_out=False, rate_limited=False)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
