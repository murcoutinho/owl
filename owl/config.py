"""Configuration loaded from environment variables.

Owl uses ~20 ``OWL_*`` env vars to configure providers, models, reviewer
slots, timeouts, and per-repo test commands. This module turns them into
a frozen ``Config`` dataclass so the runner reads from a single source of
truth and tests can inject configurations without touching ``os.environ``.

The ``.env.local`` file (if present and ``OWL_SKIP_ENV_LOCAL != "1"``) is
sourced via a small parser before the ``Config`` is built. We *do not*
shell out to ``source`` — that would let a ``.env.local`` run arbitrary
shell on import. A flat ``KEY=value`` parser is enough for our needs and
keeps the surface auditable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Provider = Literal["claude", "codex", "none"]


# ─── env-loading primitives ─────────────────────────────────────────────────


_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a ``KEY=value`` env file.

    Supported:
    * ``KEY=value`` (no quotes, value taken verbatim)
    * ``KEY="value with spaces"`` and ``KEY='value'`` (surrounding quotes stripped)
    * ``# comment`` and blank lines (skipped)
    * ``export KEY=value`` (the ``export`` prefix is stripped)

    Not supported (intentional — keeps the parser auditable):
    * Variable interpolation (``$OTHER``)
    * Multi-line values
    * Command substitution
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_env_local(env_local_path: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Merge ``env_local_path`` into ``env`` (or os.environ if not given).

    Returns a new dict; does not mutate ``env``. ``OWL_SKIP_ENV_LOCAL=1``
    short-circuits the load.
    """
    base: dict[str, str] = dict(env if env is not None else os.environ)
    if base.get("OWL_SKIP_ENV_LOCAL") == "1":
        return base
    if not env_local_path.exists():
        return base
    fields = parse_env_file(env_local_path.read_text())
    merged = dict(base)
    for k, v in fields.items():
        merged.setdefault(k, v)  # existing env wins; .env.local fills gaps
    return merged


# ─── LLM slot helper ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LLMSlot:
    provider: Provider
    model: str
    label: str = ""

    @property
    def enabled(self) -> bool:
        if self.provider == "none" or not self.provider:
            return False
        return self.model not in ("", "none")


def _normalize_provider(value: str) -> Provider:
    v = (value or "").strip().lower()
    if v in ("claude", "codex", "none"):
        return v  # type: ignore[return-value]
    return "claude"


# ─── main Config ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Config:
    target_repos: tuple[str, ...]
    project_dir: Path

    impl: LLMSlot
    fix: LLMSlot
    reviewer1: LLMSlot
    reviewer2: LLMSlot

    review_iterations: int
    max_review_rounds: int
    review_mode: Literal["parallel", "sequential"]

    llm_timeout: int
    retry_wait: int
    max_retries: int
    fix_failure_cap: int

    poll_interval: int
    pr_prefix: str
    skip_low_priority: bool

    test_cmd: Mapping[str, str] = field(default_factory=dict)
    test_setup: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, project_dir: Path | None = None) -> Config:
        target_repos = tuple(env.get("OWL_TARGET_REPOS", "").split())
        project_dir = project_dir or Path(env.get("OWL_PROJECT_DIR", "")).expanduser() or Path.cwd()

        impl_provider = _normalize_provider(env.get("OWL_IMPL_PROVIDER", "claude"))
        impl_model = env.get("OWL_IMPL_MODEL", "claude-sonnet-4-6")
        impl = LLMSlot(provider=impl_provider, model=impl_model)

        fix_provider = _normalize_provider(env.get("OWL_FIX_PROVIDER", impl_provider))
        fix_model = env.get("OWL_FIX_MODEL", impl_model)
        fix = LLMSlot(provider=fix_provider, model=fix_model)

        reviewer1 = LLMSlot(
            provider=_normalize_provider(env.get("OWL_REVIEWER1_PROVIDER", "claude")),
            model=env.get("OWL_REVIEWER1_MODEL", "claude-sonnet-4-6"),
            label=env.get("OWL_REVIEWER1_LABEL", "Claude Code 1"),
        )
        reviewer2 = LLMSlot(
            provider=_normalize_provider(env.get("OWL_REVIEWER2_PROVIDER", "claude")),
            model=env.get("OWL_REVIEWER2_MODEL", "claude-sonnet-4-6"),
            label=env.get("OWL_REVIEWER2_LABEL", "Claude Code 2"),
        )

        review_mode_raw = (env.get("OWL_REVIEW_MODE") or "parallel").strip().lower()
        review_mode: Literal["parallel", "sequential"] = (
            "sequential" if review_mode_raw == "sequential" else "parallel"
        )

        test_cmd: dict[str, str] = {}
        test_setup: dict[str, str] = {}
        for repo in target_repos:
            key = repo.replace("-", "_")
            cmd = env.get(f"OWL_TEST_CMD_{key}")
            if cmd:
                test_cmd[repo] = cmd
            setup = env.get(f"OWL_TEST_SETUP_{key}")
            if setup:
                test_setup[repo] = setup

        return cls(
            target_repos=target_repos,
            project_dir=Path(project_dir),
            impl=impl,
            fix=fix,
            reviewer1=reviewer1,
            reviewer2=reviewer2,
            review_iterations=_int(env, "REVIEW_ITERATIONS", 2, minimum=1),
            max_review_rounds=_int(env, "MAX_REVIEW_ROUNDS", 3, minimum=1),
            review_mode=review_mode,
            llm_timeout=_int(env, "OWL_LLM_TIMEOUT", 2400, minimum=10),
            retry_wait=_int(env, "RETRY_WAIT", 300, minimum=1),
            max_retries=_int(env, "MAX_RETRIES", 2, minimum=0),
            fix_failure_cap=_int(env, "OWL_FIX_FAILURE_CAP", 3, minimum=1),
            poll_interval=_int(env, "OWL_POLL_INTERVAL_SECONDS", 600, minimum=10),
            pr_prefix=env.get("OWL_PR_PREFIX", "[owl] "),
            skip_low_priority=env.get("OWL_SKIP_LOW_PRIORITY", "0") == "1",
            test_cmd=test_cmd,
            test_setup=test_setup,
        )


def _int(env: Mapping[str, str], key: str, default: int, *, minimum: int) -> int:
    raw = env.get(key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)
