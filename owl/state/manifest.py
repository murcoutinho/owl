"""Read and write owl's TSV manifest files.

Two TSV formats live in plan_work_dir:

* ``execution_base.tsv`` — written once at plan start. Three columns:
  ``repo_name<TAB>repo_root<TAB>base_hash``. One row per target repo.
* ``review_input_N.tsv`` — written after each round. Four columns:
  ``repo_name<TAB>repo_root<TAB>base_hash<TAB>after_hash``. A repo can
  appear on multiple rows across rounds; the **last row per repo** is
  authoritative.

The ``NONE`` sentinel is used when a repo has no commits yet.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BaseEntry:
    repo_name: str
    repo_root: str
    base_hash: str  # "NONE" sentinel preserved


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    repo_name: str
    repo_root: str
    base_hash: str
    after_hash: str


# ─── execution_base.tsv ─────────────────────────────────────────────────────


def read_base_tsv(path: Path) -> list[BaseEntry]:
    if not path.exists():
        return []
    out: list[BaseEntry] = []
    for row in _iter_rows(path):
        if len(row) >= 3:
            out.append(BaseEntry(repo_name=row[0], repo_root=row[1], base_hash=row[2]))
    return out


def write_base_tsv(path: Path, entries: list[BaseEntry]) -> None:
    lines = [f"{e.repo_name}\t{e.repo_root}\t{e.base_hash}\n" for e in entries]
    path.write_text("".join(lines))


# ─── review_input_N.tsv ─────────────────────────────────────────────────────


def read_manifest(path: Path) -> list[ManifestEntry]:
    """Return every row in ``review_input_N.tsv`` in file order."""
    if not path.exists():
        return []
    out: list[ManifestEntry] = []
    for row in _iter_rows(path):
        if len(row) >= 4:
            out.append(
                ManifestEntry(
                    repo_name=row[0],
                    repo_root=row[1],
                    base_hash=row[2],
                    after_hash=row[3],
                )
            )
    return out


def read_last_per_repo(path: Path) -> dict[str, ManifestEntry]:
    """Return the last row per ``repo_name``."""
    last: dict[str, ManifestEntry] = {}
    for entry in read_manifest(path):
        last[entry.repo_name] = entry
    return last


def append_manifest_row(path: Path, entry: ManifestEntry) -> None:
    line = (
        f"{entry.repo_name}\t{entry.repo_root}\t{entry.base_hash}\t{entry.after_hash}\n"
    )
    with path.open("a") as f:
        f.write(line)


def write_manifest(path: Path, entries: list[ManifestEntry]) -> None:
    """Replace the file contents with these entries (used for round seeding)."""
    lines = [
        f"{e.repo_name}\t{e.repo_root}\t{e.base_hash}\t{e.after_hash}\n" for e in entries
    ]
    path.write_text("".join(lines))


# ─── commits.tsv and pull_requests.tsv ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommitRow:
    repo_name: str
    short_hash: str


@dataclass(frozen=True, slots=True)
class PrRow:
    repo_name: str
    pr_url: str


def append_commit(path: Path, repo_name: str, short_hash: str) -> None:
    with path.open("a") as f:
        f.write(f"{repo_name}\t{short_hash}\n")


def read_commits(path: Path) -> list[CommitRow]:
    if not path.exists():
        return []
    return [
        CommitRow(repo_name=row[0], short_hash=row[1])
        for row in _iter_rows(path)
        if len(row) >= 2
    ]


def append_pr(path: Path, repo_name: str, pr_url: str) -> None:
    with path.open("a") as f:
        f.write(f"{repo_name}\t{pr_url}\n")


def read_prs(path: Path) -> list[PrRow]:
    if not path.exists():
        return []
    return [
        PrRow(repo_name=row[0], pr_url=row[1])
        for row in _iter_rows(path)
        if len(row) >= 2
    ]


# ─── helpers ────────────────────────────────────────────────────────────────


def _iter_rows(path: Path):
    with path.open(newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if row:
                yield row
