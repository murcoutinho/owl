"""Unit tests for owl.state.manifest.

The ``review_input_N.tsv`` last-line-per-repo aggregation is load-bearing —
drift detection (owl.sh:1796-1841) and per-round diff calculation both
depend on it. These tests pin that contract.
"""

from __future__ import annotations

from pathlib import Path

from owl.state.manifest import (
    BaseEntry,
    CommitRow,
    ManifestEntry,
    PrRow,
    append_commit,
    append_manifest_row,
    append_pr,
    read_base_tsv,
    read_commits,
    read_last_per_repo,
    read_manifest,
    read_prs,
    write_base_tsv,
    write_manifest,
)


def test_base_tsv_round_trip(tmp_path: Path):
    p = tmp_path / "execution_base.tsv"
    entries = [
        BaseEntry("saudade", "/Users/x/saudade", "abc123"),
        BaseEntry("saudade-mobile", "/Users/x/saudade-mobile", "NONE"),
    ]
    write_base_tsv(p, entries)
    assert p.read_text() == (
        "saudade\t/Users/x/saudade\tabc123\n"
        "saudade-mobile\t/Users/x/saudade-mobile\tNONE\n"
    )
    assert read_base_tsv(p) == entries


def test_base_tsv_returns_empty_when_missing(tmp_path: Path):
    assert read_base_tsv(tmp_path / "nope") == []


def test_manifest_appended_rows_round_trip(tmp_path: Path):
    p = tmp_path / "review_input_1.tsv"
    e1 = ManifestEntry("saudade", "/r1", "base1", "after1")
    e2 = ManifestEntry("raven", "/r2", "base2", "after2")
    append_manifest_row(p, e1)
    append_manifest_row(p, e2)
    assert read_manifest(p) == [e1, e2]


def test_manifest_last_per_repo_takes_final_row(tmp_path: Path):
    """A repo can appear multiple times — only the last row counts."""
    p = tmp_path / "review_input_2.tsv"
    e1 = ManifestEntry("saudade", "/r", "base", "after_round1")
    e2 = ManifestEntry("saudade", "/r", "base", "after_round2")
    e3 = ManifestEntry("raven", "/raven", "NONE", "first_commit")
    append_manifest_row(p, e1)
    append_manifest_row(p, e2)
    append_manifest_row(p, e3)

    last = read_last_per_repo(p)
    assert last["saudade"] == e2
    assert last["raven"] == e3
    assert len(last) == 2


def test_manifest_none_sentinel_preserved(tmp_path: Path):
    """The bash side writes ``NONE`` when a repo has no prior HEAD; we keep it."""
    p = tmp_path / "review_input_1.tsv"
    e = ManifestEntry("raven", "/r", "NONE", "abc123")
    append_manifest_row(p, e)
    [back] = read_manifest(p)
    assert back.base_hash == "NONE"


def test_manifest_write_replaces_file(tmp_path: Path):
    p = tmp_path / "review_input_3.tsv"
    p.write_text("stale\trow\n")
    e = ManifestEntry("saudade", "/r", "b", "a")
    write_manifest(p, [e])
    assert read_manifest(p) == [e]


def test_manifest_read_missing_returns_empty(tmp_path: Path):
    assert read_manifest(tmp_path / "nope") == []
    assert read_last_per_repo(tmp_path / "nope") == {}


def test_manifest_skips_rows_with_too_few_columns(tmp_path: Path):
    p = tmp_path / "review_input_1.tsv"
    p.write_text(
        "saudade\t/r\tbase\tafter\n"
        "malformed\trow\n"  # only 2 cols — skipped
        "raven\t/r\tNONE\tcommit\n"
    )
    assert len(read_manifest(p)) == 2


# ─── commits.tsv ────────────────────────────────────────────────────────────


def test_commits_append_and_read(tmp_path: Path):
    p = tmp_path / "commits.tsv"
    append_commit(p, "saudade", "abc1234")
    append_commit(p, "raven", "def5678")
    assert read_commits(p) == [
        CommitRow("saudade", "abc1234"),
        CommitRow("raven", "def5678"),
    ]


def test_commits_read_missing_returns_empty(tmp_path: Path):
    assert read_commits(tmp_path / "nope") == []


# ─── pull_requests.tsv ──────────────────────────────────────────────────────


def test_prs_append_and_read(tmp_path: Path):
    p = tmp_path / "pull_requests.tsv"
    append_pr(p, "saudade", "https://github.com/x/saudade/pull/1")
    append_pr(p, "raven", "https://github.com/x/raven/pull/9")
    assert read_prs(p) == [
        PrRow("saudade", "https://github.com/x/saudade/pull/1"),
        PrRow("raven", "https://github.com/x/raven/pull/9"),
    ]


def test_prs_read_missing_returns_empty(tmp_path: Path):
    assert read_prs(tmp_path / "nope") == []
