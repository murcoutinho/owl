"""Dependency container injected into the plan runner and review loop.

Bundling git/llm/clock/log/ack behind one object is what makes the state
machine testable: production wires real implementations, tests pass fakes.
Nothing in ``plan_runner`` or ``review`` imports git/subprocess modules
directly — they go through ``Deps``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .git_ops import GitClient, GitLike
from .subprocess_.llm import LLMResult


class LLMRunnerLike(Protocol):
    def run(
        self,
        provider: str,
        model: str,
        prompt_path: Path,
        *,
        session_id: str | None = None,
        session_mode: str = "create",
        cwd: Path | None = None,
    ) -> LLMResult: ...


def _default_now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _default_git(repo_root: Path) -> GitLike:
    return GitClient(repo_root)


def _noop_ack(*_args: Any, **_kwargs: Any):
    return nullcontext()


@dataclass(frozen=True, slots=True)
class Deps:
    cfg: Config
    llm: LLMRunnerLike
    git: Callable[[Path], GitLike] = _default_git
    now: Callable[[], str] = _default_now
    log: Callable[[str], None] = field(default=lambda _m: None)
    # ack_watcher(label, ack_path) -> context manager
    ack_watcher: Callable[..., Any] = _noop_ack
