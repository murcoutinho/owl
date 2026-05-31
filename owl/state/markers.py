"""Read and write the ``key=value`` marker files in plan_work_dir.

Owl persists state to plain text files. Three of them follow a shared
``key=value\\n`` block format:

* ``dirty_after_fix_failure`` — written when a fix phase ran the LLM but
  failed to commit, contains ``attempt``, ``iteration``, ``reason``,
  ``recorded_at``.
* ``pending_status`` — written when the review loop aborts mid-run, tells
  the next cycle whether to retry the whole cycle (``llm_failure``) or
  just skip this plan (``dirty_fix``).
* ``quarantined`` — written after FIX_FAILURE_CAP attempts, records the
  quarantine location and reason.

The parser tolerates extra unknown keys (forward-compat with future
bash → python migrations), trailing newlines, and reads/writes byte-for-byte
the same format as the bash implementation. That on-disk compatibility is
what lets an in-flight queue survive the cutover.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, ClassVar, Literal


def parse_kv(text: str) -> dict[str, str]:
    """Parse a ``key=value\\n`` block. Tolerates blank lines and trailing newlines.

    Values may contain ``=`` characters — we only split on the first one.
    Unknown keys are returned in the dict so the caller can decide whether
    to ignore them.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def dump_kv(items: dict[str, Any]) -> str:
    """Serialize a dict to ``key=value\\n`` lines, in insertion order.

    Values that are ``None`` are dropped (we never want a literal ``key=None``
    line on disk). Other values are str()-converted.
    """
    return "".join(f"{k}={v}\n" for k, v in items.items() if v is not None)


# ─── DirtyAfterFix ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DirtyAfterFix:
    """The ``dirty_after_fix_failure`` marker."""

    attempt: int
    iteration: int
    reason: str
    recorded_at: str

    # Keys we expect on disk, in order, for deterministic writes.
    _KEYS: ClassVar[tuple[str, ...]] = ("attempt", "iteration", "reason", "recorded_at")

    def to_text(self) -> str:
        return dump_kv({k: getattr(self, k) for k in self._KEYS})

    @classmethod
    def from_text(cls, text: str) -> DirtyAfterFix:
        kv = parse_kv(text)
        return cls(
            attempt=int(kv.get("attempt", "0") or "0"),
            iteration=int(kv.get("iteration", "0") or "0"),
            reason=kv.get("reason", ""),
            recorded_at=kv.get("recorded_at", ""),
        )

    @classmethod
    def read(cls, path: Path) -> DirtyAfterFix | None:
        if not path.exists():
            return None
        return cls.from_text(path.read_text())

    def write(self, path: Path) -> None:
        path.write_text(self.to_text())


# ─── PendingStatus ──────────────────────────────────────────────────────────


PendingCategory = Literal["dirty_fix", "llm_failure"]


@dataclass(frozen=True, slots=True)
class PendingStatus:
    """The ``pending_status`` marker — written when the review loop aborts."""

    plan_name: str
    plan_file: str
    branch_name: str
    failed_iteration: int
    total_iterations: int
    reviews_done: int
    fix_attempts: int
    reason: str
    category: PendingCategory
    aborted_at: str

    _KEYS: ClassVar[tuple[str, ...]] = (
        "plan_name",
        "plan_file",
        "branch_name",
        "failed_iteration",
        "total_iterations",
        "reviews_done",
        "fix_attempts",
        "reason",
        "category",
        "aborted_at",
    )

    def to_text(self) -> str:
        return dump_kv({k: getattr(self, k) for k in self._KEYS})

    @classmethod
    def from_text(cls, text: str) -> PendingStatus:
        kv = parse_kv(text)
        category = kv.get("category", "dirty_fix")
        if category not in ("dirty_fix", "llm_failure"):
            category = "dirty_fix"
        return cls(
            plan_name=kv.get("plan_name", ""),
            plan_file=kv.get("plan_file", ""),
            branch_name=kv.get("branch_name", ""),
            failed_iteration=int(kv.get("failed_iteration", "0") or "0"),
            total_iterations=int(kv.get("total_iterations", "0") or "0"),
            reviews_done=int(kv.get("reviews_done", "0") or "0"),
            fix_attempts=int(kv.get("fix_attempts", "0") or "0"),
            reason=kv.get("reason", ""),
            category=category,  # type: ignore[arg-type]
            aborted_at=kv.get("aborted_at", ""),
        )

    @classmethod
    def read(cls, path: Path) -> PendingStatus | None:
        if not path.exists():
            return None
        return cls.from_text(path.read_text())

    def write(self, path: Path) -> None:
        path.write_text(self.to_text())


# ─── Quarantined ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Quarantined:
    """The ``quarantined`` marker — written after FIX_FAILURE_CAP attempts."""

    plan_name: str
    plan_file: str
    status: str
    reason: str
    fix_attempts: int
    quarantine_file: str
    quarantined_at: str

    _KEYS: ClassVar[tuple[str, ...]] = (
        "plan_name",
        "plan_file",
        "status",
        "reason",
        "fix_attempts",
        "quarantine_file",
        "quarantined_at",
    )

    def to_text(self) -> str:
        return dump_kv({k: getattr(self, k) for k in self._KEYS})

    @classmethod
    def from_text(cls, text: str) -> Quarantined:
        kv = parse_kv(text)
        return cls(
            plan_name=kv.get("plan_name", ""),
            plan_file=kv.get("plan_file", ""),
            status=kv.get("status", "quarantined"),
            reason=kv.get("reason", ""),
            fix_attempts=int(kv.get("fix_attempts", "0") or "0"),
            quarantine_file=kv.get("quarantine_file", ""),
            quarantined_at=kv.get("quarantined_at", ""),
        )

    @classmethod
    def read(cls, path: Path) -> Quarantined | None:
        if not path.exists():
            return None
        return cls.from_text(path.read_text())

    def write(self, path: Path) -> None:
        path.write_text(self.to_text())


# ─── Generic dataclass-as-dict helpers (used in tests) ──────────────────────


def asdict_skip_none(obj: Any) -> dict[str, Any]:
    """Return ``dataclasses.asdict`` minus keys whose value is ``None``."""
    return {k: v for k, v in asdict(obj).items() if v is not None}


def dataclass_field_names(klass: type) -> tuple[str, ...]:
    return tuple(f.name for f in fields(klass))
