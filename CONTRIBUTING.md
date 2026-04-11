# Contributing to Owl

Thanks for taking the time to look at Owl. This document covers what's easy to
land, what to expect from reviews, and the hygiene the repo enforces.

## Ways to contribute

- **Bug reports.** Reproduction steps matter more than severity. If you can
  name the commit where the behavior changed, even better.
- **Regression tests.** If you've hit a bug and have a narrow repro, a failing
  bash test in `tests/` is the most useful PR shape. See the existing files
  under `tests/` for the fixture pattern — `tests/lib.sh` has reusable helpers
  for spinning up fake projects and sourcing `owl.sh` without running its
  poll loop.
- **New provider integrations.** The `run_llm` dispatcher in `src/owl.sh` is
  where new providers plug in. A new provider should honor `RETRY_WAIT` /
  `MAX_RETRIES` and surface rate-limit exhaustion as a non-zero exit so the
  existing abort-and-resume path kicks in.
- **Documentation.** The README and the plan-author skill both drift as
  behavior changes. Noticing an out-of-date paragraph is a valid PR.

## What's unlikely to land

- Adding configuration knobs for things one `.env.local` variable already
  controls.
- Refactors that churn `owl.sh` without a failing test or a concrete bug.
- Anything that weakens the review loop's guarantee that failing deterministic
  tests block the LGTM early-exit.
- Features that require merging PRs on the user's behalf. Owl opens PRs and
  stops there by design.

## Development setup

```bash
git clone https://github.com/murcoutinho/owl.git
cd owl
./src/owl.sh --doctor   # verifies CLIs, auth, and target repos
```

You don't need `claude`, `codex`, or `gh` installed to run the test suite —
the fixtures stub them. You only need the real CLIs to actually run the
agent end-to-end.

## Tests

```bash
tests/run_tests.sh
```

All tests must pass before a PR is mergeable. If you add a new file under
`tests/`, the runner picks it up automatically as long as it's executable
and named `test_*.sh`.

Test conventions:

- Use `setup_fake_project` / `source_owl` from `tests/lib.sh` to avoid
  touching the user's real repos.
- Prefer `assert_*` helpers over ad-hoc `if ... exit 1` blocks so failures
  print a useful message.
- Tests should not depend on `OWL_TARGET_REPOS` being set in the host env —
  `source_owl` reassigns it after sourcing.

## Shellcheck

`src/owl.sh` and the test files are linted by shellcheck on every push via
`.github/workflows/shellcheck.yml`. Run it locally before opening a PR:

```bash
shellcheck src/owl.sh tests/*.sh
```

If shellcheck flags something you think is a false positive, prefer a
targeted `# shellcheck disable=SCxxxx` with a one-line comment explaining
why over disabling the check globally.

## Commit style

- Short imperative subject, wrapped at ~72 characters.
- Body explains the **why** — what bug this fixes, what incident prompted it,
  what alternative you ruled out. Don't restate the diff.
- One logical change per commit. If a refactor and a bug fix are tangled,
  split them.

## Releasing

Owl has no release cadence — `main` is the rolling release. Any merged commit
is considered shippable, which is why the test suite and shellcheck gate
are non-negotiable.
