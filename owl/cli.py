"""Command-line entry point.

Implements ``--validate <plan>``, ``--doctor``, and (placeholder) ``--run-plan``.
The full queue runner and plan-execution machinery is built in subsequent
modules; this CLI ships first so the operator can run ``--validate`` and
``--doctor`` against the new Python implementation while bash still drives
the production queue.

Exit codes:

* ``0`` — success
* ``1`` — doctor reported failures, or generic runtime error
* ``2`` — invalid arguments or invalid plan
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config, load_env_local
from .doctor import format_report as format_doctor
from .doctor import run_doctor
from .validate import format_report as format_validate
from .validate import validate_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owl",
        description="Agentic plan queue. See the README for the lifecycle.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate",
        metavar="PLAN",
        help="Parse and validate a plan file without touching git or any LLM.",
    )
    mode.add_argument(
        "--doctor",
        action="store_true",
        help="Check CLIs, credentials, and configured target repos.",
    )
    mode.add_argument(
        "--run-plan",
        metavar="PLAN",
        help="Run a single plan and exit. Acquires only a per-plan lock.",
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run one queue cycle (resume + new plans) and exit. No poll loop.",
    )
    parser.add_argument(
        "--skip-low-priority",
        action="store_true",
        help="In queue mode, skip plans whose frontmatter declares 'priority: low'.",
    )
    parser.add_argument(
        "--include-low-priority",
        action="store_true",
        help="Force-include low-priority plans even if OWL_SKIP_LOW_PRIORITY=1.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    env = load_env_local(Path(".env.local"))
    cfg = Config.from_env(env)

    if args.validate:
        plan_path = Path(args.validate)
        report = validate_plan(plan_path, cfg)
        print(format_validate(report))
        return 0 if report.ok else 2

    if args.doctor:
        report = run_doctor(cfg)
        print(format_doctor(report))
        return 0 if report.all_ok else 1

    if args.run_plan:
        return _run_single_plan(Path(args.run_plan), cfg)

    if args.once:
        return _run_queue_once(cfg)

    # Default: queue loop. Matches `./src/owl.sh` with no flags.
    return _run_queue_loop(cfg)


def _build_runtime_deps(cfg: Config):
    from .deps import Deps
    from .log import log as log_fn
    from .subprocess_.llm import LLMRunner

    runner = LLMRunner(
        timeout=cfg.llm_timeout,
        max_retries=cfg.max_retries,
        retry_wait=cfg.retry_wait,
        log=log_fn,
    )
    return Deps(cfg=cfg, llm=runner, log=log_fn), log_fn


def _run_single_plan(plan_path: Path, cfg: Config) -> int:
    """Run exactly one plan via the Python runner and report the outcome."""
    from .plans.model import Plan
    from .runner import PlanRunOutcome, run_plan

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    deps, log_fn = _build_runtime_deps(cfg)
    plan = Plan.load(
        plan_path,
        default_review_rounds=cfg.review_iterations,
        max_review_rounds=cfg.max_review_rounds,
    )
    result = run_plan(plan, deps, work_root=cfg.work_dir)
    log_fn(f"Plan '{plan.name}' finished: {result.outcome}")
    return 0 if result.outcome != PlanRunOutcome.INVALID else 2


def _run_queue_once(cfg: Config) -> int:
    """One queue cycle: resume pending, then drain new plans, exit."""
    from .runner import check_plans

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.plan_dir.mkdir(parents=True, exist_ok=True)
    deps, log_fn = _build_runtime_deps(cfg)
    log_fn(f"Owl (python) — single cycle. plan_dir={cfg.plan_dir} work_dir={cfg.work_dir}")
    check_plans(deps, work_root=cfg.work_dir, plan_dir=cfg.plan_dir)
    return 0


def _run_queue_loop(cfg: Config) -> int:
    """Default mode: loop forever, polling for plans every poll_interval seconds.

    Matches bash ``./src/owl.sh`` with no flags. Ctrl-C is the clean exit.
    """
    import time

    from .runner import check_plans

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.plan_dir.mkdir(parents=True, exist_ok=True)
    deps, log_fn = _build_runtime_deps(cfg)
    log_fn(f"Owl (python) — queue loop. plan_dir={cfg.plan_dir} work_dir={cfg.work_dir}")
    log_fn(f"Poll interval: {cfg.poll_interval}s. Ctrl-C to stop.")
    try:
        while True:
            check_plans(deps, work_root=cfg.work_dir, plan_dir=cfg.plan_dir)
            log_fn(f"Cycle complete. Sleeping {cfg.poll_interval}s...")
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        log_fn("Interrupted. Exiting.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
