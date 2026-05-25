"""``owl --doctor``: preflight checks for CLIs, credentials, and target repos.

Reports each check as ``ok`` or ``FAIL`` to stdout. Exit code is 0 if every
check passes, 1 otherwise. The shell side checks (owl.sh:2522-2604):

* ``claude``, ``codex``, ``gh``, ``git`` are on ``$PATH``
* ``gh auth status`` succeeds
* ``OWL_TARGET_REPOS`` is set
* Each target repo exists under ``$PROJECT_DIR/<repo_name>`` and is a git repo

The Python port keeps the same shape so a side-by-side diff against the
bash version produces near-identical text.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True, slots=True)
class Check:
    label: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: list[Check]

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)


def run_doctor(cfg: Config, *, runner=None) -> DoctorReport:
    """Run all checks and return a structured report.

    ``runner`` is an optional injectable subprocess wrapper. When ``None``
    we use ``subprocess.run``; tests pass a fake.
    """
    run = runner or _default_runner
    checks: list[Check] = []

    # CLI availability
    for binary in ("git", "claude", "codex", "gh"):
        path = shutil.which(binary)
        checks.append(
            Check(
                label=f"  {binary} on PATH",
                ok=path is not None,
                detail=path or "(not found)",
            )
        )

    # gh auth (only meaningful if gh is on PATH)
    if shutil.which("gh"):
        rc, _ = run(["gh", "auth", "status"])
        checks.append(
            Check(
                label="  gh auth status",
                ok=rc == 0,
                detail="logged in" if rc == 0 else "not logged in (run `gh auth login`)",
            )
        )

    # OWL_TARGET_REPOS
    if cfg.target_repos:
        checks.append(
            Check(
                label=f'  OWL_TARGET_REPOS="{" ".join(cfg.target_repos)}"',
                ok=True,
            )
        )
    else:
        checks.append(
            Check(
                label="  OWL_TARGET_REPOS is not set",
                ok=False,
                detail="example: export OWL_TARGET_REPOS='my-repo other-repo'",
            )
        )

    # Per-repo existence
    for repo_name in cfg.target_repos:
        repo_root = cfg.project_dir / repo_name
        if not repo_root.exists():
            checks.append(
                Check(label=f"  {repo_name}: missing", ok=False, detail=str(repo_root))
            )
            continue
        rc, _ = run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"]
        )
        checks.append(
            Check(
                label=f"  {repo_name}: git repo",
                ok=rc == 0,
                detail=str(repo_root) if rc == 0 else "not a git repo",
            )
        )

    return DoctorReport(checks=checks)


def format_report(report: DoctorReport) -> str:
    lines: list[str] = ["Owl doctor:"]
    for c in report.checks:
        marker = "ok" if c.ok else "FAIL"
        lines.append(f"  {marker}  {c.label.lstrip()}    {c.detail}".rstrip())
    lines.append("")
    lines.append("All checks passed." if report.all_ok else "One or more checks failed.")
    return "\n".join(lines)


# ─── default runner ─────────────────────────────────────────────────────────


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return result.returncode, result.stdout
