"""GitHub CLI operations (``gh``), behind one object so tests can fake it.

Only two operations are needed: opening a PR and checking auth. ``GhLike`` is
the protocol the PR step depends on; tests inject ``FakeGh``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PrResult:
    ok: bool
    url: str
    error: str = ""


@runtime_checkable
class GhLike(Protocol):
    def pr_create(self, repo_root: Path, *, base: str, title: str, body: str) -> PrResult: ...
    def auth_ok(self) -> bool: ...


class GhClient:
    def __init__(self, *, runner=None):
        self._runner = runner or _subprocess_runner

    def pr_create(self, repo_root: Path, *, base: str, title: str, body: str) -> PrResult:
        rc, out = self._runner(
            ["gh", "pr", "create", "--base", base, "--title", title, "--body", body],
            cwd=repo_root,
        )
        url = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0 and url.startswith("https://"):
            return PrResult(ok=True, url=url)
        return PrResult(ok=False, url="", error=out.strip())

    def auth_ok(self) -> bool:
        rc, _ = self._runner(["gh", "auth", "status"], cwd=None)
        return rc == 0


def _subprocess_runner(argv: list[str], *, cwd: Path | None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return 127, "gh not found"
    return proc.returncode, proc.stdout + proc.stderr
