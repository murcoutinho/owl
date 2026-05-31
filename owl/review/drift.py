"""Manifest drift detection and auto-heal on resume.

Ports the resume drift block. When resuming, each repo's
actual HEAD is compared to the last-recorded head in the next-iteration
manifest. If they differ (an interrupted commit landed, a manual amend, etc.),
git is the source of truth: we append a corrected line so the diff range
becomes ``base..actual_head``. Non-destructive — earlier lines stay, and every
reader takes the last line per repo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..git_ops import GitLike
from ..state import manifest
from ..state.manifest import ManifestEntry


@dataclass(frozen=True, slots=True)
class DriftResult:
    healed: list[ManifestEntry]
    notes: list[str]

    @property
    def any_drift(self) -> bool:
        return bool(self.healed or self.notes)


def heal_manifest(
    manifest_path: Path,
    *,
    git: Callable[[Path], GitLike],
    log: Callable[[str], None] = lambda _m: None,
) -> DriftResult:
    """Detect drift in ``manifest_path`` and append corrected lines in place."""
    healed: list[ManifestEntry] = []
    notes: list[str] = []

    last_per_repo = manifest.read_last_per_repo(manifest_path)
    for entry in last_per_repo.values():
        repo_root = Path(entry.repo_root)
        g = git(repo_root)
        if not g.is_inside_work_tree():
            log(f"[Resume] {entry.repo_name}: repo directory missing at {repo_root}")
            notes.append(f"- {entry.repo_name}: repo directory missing at {repo_root}")
            continue
        actual = g.head_hash() or ""
        if actual and actual != entry.after_hash:
            log(
                f"[Resume] {entry.repo_name}: HEAD={actual} differs from recorded "
                f"manifest head={entry.after_hash} — auto-healing to actual HEAD."
            )
            corrected = ManifestEntry(
                repo_name=entry.repo_name,
                repo_root=entry.repo_root,
                base_hash=entry.base_hash,
                after_hash=actual,
            )
            healed.append(corrected)
            notes.append(
                f"- {entry.repo_name}: expected head {entry.after_hash}, "
                f"actual {actual} — auto-healed."
            )

    for entry in healed:
        manifest.append_manifest_row(manifest_path, entry)

    if healed or notes:
        log(
            f"[Resume] Drift detected on {len(healed)} repo(s). "
            "Manifest auto-healed; proceeding into the review iteration."
        )
    return DriftResult(healed=healed, notes=notes)
