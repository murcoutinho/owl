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
import sys
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
        # Queue runner / plan executor lands in the next slice of the rewrite.
        # Until then, --run-plan is not implemented in the Python port — fall
        # back to bash from your shell if you need it now.
        print(
            "owl: --run-plan is not yet implemented in the Python port. "
            "Use ./src/owl.sh --run-plan <plan> for now.",
            file=sys.stderr,
        )
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
