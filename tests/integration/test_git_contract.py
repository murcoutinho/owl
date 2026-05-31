"""Contract test: GitClient (real git) and FakeGit agree on observable behavior.

The state machine is written against the GitLike protocol. If FakeGit and
GitClient diverge, the state-machine tests pass while production breaks.
This test runs the same logical operations against both and asserts the
observable results match.

Marked ``integration`` because it spawns real git.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from owl.git_ops import GitClient, GitLike

pytestmark = pytest.mark.integration


# ─── real GitClient behavior ────────────────────────────────────────────────


def test_real_client_is_git_like(real_git: GitClient):
    assert isinstance(real_git, GitLike)


def test_real_client_inspection(real_git: GitClient):
    assert real_git.is_inside_work_tree() is True
    assert real_git.has_commits() is True
    assert real_git.current_branch() == "main"
    assert real_git.head_hash() is not None
    assert real_git.short_head() is not None
    assert real_git.has_local_changes() is False


def test_real_client_detects_untracked(real_git: GitClient, real_repo: Path):
    (real_repo / "new.txt").write_text("x\n")
    assert real_git.has_local_changes() is True
    assert "new.txt" in real_git.untracked_files()


def test_real_client_detects_tracked_modification(real_git: GitClient, real_repo: Path):
    (real_repo / "README.md").write_text("changed\n")
    assert real_git.has_local_changes() is True
    assert "README.md" in real_git.diff_name_only()


def test_real_client_create_branch_commit_moves_head(real_git: GitClient, real_repo: Path):
    before = real_git.head_hash()
    assert real_git.checkout("owl/feature", create=True) is True
    assert real_git.current_branch() == "owl/feature"

    (real_repo / "feature.py").write_text("print('hi')\n")
    assert real_git.add_all() is True
    assert real_git.commit("[owl] feature — execution") is True

    after = real_git.head_hash()
    assert after != before


def test_real_client_commit_with_nothing_staged_returns_false(real_git: GitClient):
    # Clean tree → commit is a no-op, returns False.
    assert real_git.commit("empty") is False


def test_real_client_default_branch_is_main(real_git: GitClient):
    assert real_git.default_branch() == "main"


def test_real_client_verify_ref(real_git: GitClient):
    assert real_git.verify_ref("HEAD") is True
    assert real_git.verify_ref("refs/heads/main") is True
    assert real_git.verify_ref("refs/heads/does-not-exist") is False


def test_real_client_worktree_add_remove(real_git: GitClient, tmp_path: Path):
    wt = tmp_path / "wt"
    assert real_git.worktree_add(wt) is True
    assert (wt / "README.md").exists()
    wt_client = GitClient(wt)
    assert wt_client.is_inside_work_tree() is True
    assert real_git.worktree_remove(wt) is True


def test_real_client_reset_and_clean(real_git: GitClient, real_repo: Path):
    (real_repo / "README.md").write_text("dirty\n")
    (real_repo / "untracked.txt").write_text("x\n")
    assert real_git.has_local_changes() is True
    real_git.reset_hard()
    real_git.clean()
    assert real_git.has_local_changes() is False


# ─── FakeGit mirrors the same observable behavior ───────────────────────────


def test_fake_client_is_git_like(git_universe):
    fake = git_universe.client(Path("/fake/repo"))
    assert isinstance(fake, GitLike)


def test_fake_client_create_branch_commit_moves_head(git_universe):
    root = Path("/fake/repo")
    git_universe.add_repo(root, branches={"main"}, current="main", head="hash0", dirty=False)
    fake = git_universe.client(root)

    before = fake.head_hash()
    assert fake.checkout("owl/feature", create=True) is True
    assert fake.current_branch() == "owl/feature"

    git_universe.state(root).dirty = True  # simulate the coder writing files
    assert fake.add_all() is True
    assert fake.commit("[owl] feature — execution") is True
    assert fake.head_hash() != before
    assert fake.has_local_changes() is False


def test_fake_client_commit_clean_tree_returns_false(git_universe):
    root = Path("/fake/repo")
    git_universe.add_repo(root, dirty=False)
    fake = git_universe.client(root)
    assert fake.commit("empty") is False


def test_fake_client_commit_failure_simulation(git_universe):
    """The dirty-after-fix state machine test depends on this knob."""
    root = Path("/fake/repo")
    git_universe.add_repo(root, dirty=True, commit_should_fail=True)
    fake = git_universe.client(root)
    assert fake.commit("will fail") is False
    assert fake.has_local_changes() is True  # stays dirty


def test_fake_client_checkout_missing_branch_returns_false(git_universe):
    root = Path("/fake/repo")
    git_universe.add_repo(root, branches={"main"})
    fake = git_universe.client(root)
    assert fake.checkout("owl/nonexistent") is False


def test_fake_client_worktree_add_makes_path_resolvable(git_universe, tmp_path: Path):
    src = tmp_path / "src"
    git_universe.add_repo(src, branches={"main"}, head="hash0")
    fake = git_universe.client(src)
    wt = tmp_path / "wt"
    assert fake.worktree_add(wt) is True
    wt_client = git_universe.client(wt)
    assert wt_client.is_inside_work_tree() is True
    assert wt_client.head_hash() == "hash0"
