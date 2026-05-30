# Contributing to Owl

Thanks for taking the time to look at Owl. This document covers what's easy
to land, what to expect from reviews, and the hygiene the repo enforces.

## Ways to contribute

- **Bug reports.** Reproduction steps matter more than severity. If you can
  name the commit where the behavior changed, even better.
- **Regression tests.** If you've hit a bug and have a narrow repro, a
  failing pytest test in `tests/` is the most useful PR shape. See the
  existing files under `tests/unit/`, `tests/state_machine/`, and
  `tests/integration/` for the fixture pattern — `tests/conftest.py`
  exposes the shared fixtures (fake LLM, fake git, plan workspaces).
- **New provider integrations.** The LLM dispatcher lives in
  `owl/subprocess_/llm.py`. A new provider should honor `RETRY_WAIT` /
  `MAX_RETRIES` and surface rate-limit exhaustion as a non-zero exit so
  the existing abort-and-resume path kicks in.
- **Documentation.** The README and the plan-author skill both drift as
  behavior changes. Noticing an out-of-date paragraph is a valid PR.

## What's unlikely to land

- Adding configuration knobs for things one `.env.local` variable already
  controls.
- Refactors without a failing test or a concrete bug.
- Anything that weakens the review loop's guarantee that failing
  deterministic tests block the LGTM early-exit.
- Anything that weakens the verification pass that re-reviews after the
  final fix iteration — PRs must never ship with unaddressed reviewer
  findings.
- Features that require merging PRs on the user's behalf. Owl opens PRs
  and stops there by design.

## Development setup

```bash
git clone https://github.com/YOUR_ORG/owl.git
cd owl

python3.11 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

owl --doctor   # verifies CLIs, auth, and target repos
```

You don't need `claude`, `codex`, or `gh` installed to run the test suite —
the fixtures stub them. You only need the real CLIs to actually run the
agent end-to-end.

## Tests

```bash
pytest -q              # full suite
ruff check owl/ tests/ # lint
```

Both run on every push and pull request via `.github/workflows/tests.yml`
and must pass before a PR is mergeable.

Test conventions:

- Unit tests live under `tests/unit/` and target one module at a time.
- State-machine tests under `tests/state_machine/` exercise the review
  loop, fix phase, and recovery transitions against fake LLM and fake git
  back-ends.
- Integration tests under `tests/integration/` run against real temporary
  git repositories and are marked with `@pytest.mark.integration`.
- Prefer the shared fixtures from `tests/conftest.py` (`fake_llm`,
  `git_universe`, `pending_ctx`) over building bespoke setups so failures
  print useful diagnostics.

## Commit style

- Short imperative subject, wrapped at ~72 characters.
- Body explains the **why** — what bug this fixes, what incident prompted
  it, what alternative you ruled out. Don't restate the diff.
- One logical change per commit. If a refactor and a bug fix are tangled,
  split them.

## Releasing

Owl has no release cadence — `main` is the rolling release. Any merged
commit is considered shippable, which is why the test suite and ruff lint
are non-negotiable.
