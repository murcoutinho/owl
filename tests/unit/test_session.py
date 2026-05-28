"""Unit tests for owl.state.session."""

from __future__ import annotations

import uuid
from pathlib import Path

from owl.state import session


def test_get_or_create_persists_and_reuses(tmp_path: Path):
    f = tmp_path / "coder_session_id"
    sid1 = session.get_or_create(f)
    assert f.exists()
    # Valid UUID
    uuid.UUID(sid1)
    # Second call returns the same id (not a new one)
    sid2 = session.get_or_create(f)
    assert sid1 == sid2


def test_load_returns_none_when_missing(tmp_path: Path):
    assert session.load(tmp_path / "nope") is None


def test_load_returns_persisted_id(tmp_path: Path):
    f = tmp_path / "coder_session_id"
    f.write_text("abc-123\n")
    assert session.load(f) == "abc-123"


def test_load_ignores_blank_file(tmp_path: Path):
    f = tmp_path / "coder_session_id"
    f.write_text("\n")
    assert session.load(f) is None
