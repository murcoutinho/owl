# Examples

Sample plans you can copy into `plan/` to try owl against a target repo.

Before running, make sure `OWL_TARGET_REPOS` is set to a repo you're happy to
experiment in (e.g. a sandbox or a fork).

## Plans

| File | Description |
|------|-------------|
| [001-touch-readme.md](001-touch-readme.md) | Smoke test — appends one line to README. Good first-run check. |
| [002-add-python-helper.md](002-add-python-helper.md) | Adds a small utility function with tests. Typical feature plan shape. |
| [003-stacked-plan.md](003-stacked-plan.md) | Demonstrates `base-branch:` frontmatter for stacked PRs that depend on plan 002. |

## How to run

```bash
# Copy a plan into the queue
cp examples/001-touch-readme.md plan/001-touch-readme.md

# Point the agent at your sandbox repo
export OWL_TARGET_REPOS=my-sandbox-repo

# Start the agent
./src/owl.sh
```

The agent picks up the plan on its next poll cycle (default: 10 minutes), or
immediately if you just started it.
