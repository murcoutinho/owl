"""Unit tests for owl.subprocess_.prompts (ack + worktree contract wrapping)."""

from __future__ import annotations

from pathlib import Path

from owl.subprocess_.prompts import build_wrapped_prompt, write_wrapped_prompt


def test_ack_directive_always_present():
    text = build_wrapped_prompt(ack_path=Path("/w/ack"), base_prompt="do the thing")
    assert "PROOF-OF-LIFE REQUIREMENT" in text
    assert "ACK_FILE_PATH: /w/ack" in text
    assert "do the thing" in text


def test_no_worktree_contract_when_root_missing():
    text = build_wrapped_prompt(ack_path=Path("/w/ack"), base_prompt="x")
    assert "WORKTREE CONTRACT" not in text


def test_no_worktree_contract_when_root_equals_project_dir():
    text = build_wrapped_prompt(
        ack_path=Path("/w/ack"),
        base_prompt="x",
        worktree_root=Path("/project"),
        project_dir=Path("/project"),
        sub_repos=["saudade"],
    )
    assert "WORKTREE CONTRACT" not in text


def test_worktree_contract_injected_and_lists_single_repo():
    text = build_wrapped_prompt(
        ack_path=Path("/w/ack"),
        base_prompt="x",
        worktree_root=Path("/work/wt"),
        project_dir=Path("/project"),
        sub_repos=["saudade"],
    )
    assert "WORKTREE CONTRACT" in text
    assert "/work/wt/saudade" in text
    assert "/project" in text
    # The contract appears before the base prompt
    assert text.index("WORKTREE CONTRACT") < text.index("x")


def test_write_wrapped_prompt_creates_file_and_clears_ack(tmp_path: Path):
    ack = tmp_path / "ack"
    ack.write_text("stale")  # should be cleared
    out = tmp_path / "wrapped.txt"
    write_wrapped_prompt(out, ack_path=ack, base_prompt="hello")
    assert out.exists()
    assert "hello" in out.read_text()
    assert not ack.exists()  # cleared before run
