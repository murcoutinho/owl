"""Unit tests for owl.plans.model.Plan."""

from __future__ import annotations

from pathlib import Path

from owl.plans.model import Plan


def test_load_extracts_slug_from_filename(tmp_path: Path):
    p = tmp_path / "287-server-otp.md"
    p.write_text("---\nrepo: saudade\n---\n# body\n")
    plan = Plan.load(p)
    assert plan.name == "287-server-otp.md"
    assert plan.slug == "287-server-otp"
    assert plan.path == p


def test_load_strips_frontmatter_from_body(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("---\nrepo: saudade\nreview-rounds: 2\n---\n# Title\n\nBody.\n")
    plan = Plan.load(p)
    assert plan.body == "# Title\n\nBody.\n"
    assert "review-rounds" not in plan.body


def test_load_keeps_fm_repo(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("---\nrepo: saudade\n---\n")
    plan = Plan.load(p)
    assert plan.fm.repo == "saudade"


def test_load_with_no_frontmatter_keeps_full_body(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("just a body\n")
    plan = Plan.load(p)
    assert plan.body == "just a body\n"
    assert plan.fm.repo is None
