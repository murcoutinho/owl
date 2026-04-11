---
review-rounds: 1
---

<!-- This is a sample plan. Copy it to `plan/` to try it against your own target repo.
     Make sure `OWL_TARGET_REPOS` points at a repo you're happy to experiment in. -->

# Touch README

## Context

This is a smoke-test plan. It makes the smallest possible change — appending a
single line to the target repo's README — to verify that the agent can find the
repo, make a change, commit it, and open a PR without errors.

## Goal

Append the following line to the end of `README.md` in the target repo:

```
<!-- owl smoke test -->
```

That's the only change. Do not modify anything else.

## Verification

- `README.md` ends with `<!-- owl smoke test -->`
- No other files are modified
