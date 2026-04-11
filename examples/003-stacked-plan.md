---
base-branch: owl/002-add-python-helper
---

<!-- This is a sample plan. Copy it to `plan/` to try it against your own target repo.
     Make sure `OWL_TARGET_REPOS` points at a repo you're happy to experiment in. -->

# Extend truncate helper with word-boundary support

## Context

This plan depends on plan 002 (`002-add-python-helper`). It extends the
`truncate` function added in that plan with an optional `word_boundary` flag
that prevents cutting in the middle of a word.

**This plan demonstrates the `base-branch:` frontmatter feature.** The
`base-branch: owl/002-add-python-helper` declaration above tells owl to check
out plan 002's branch before executing this plan, so the two changes stack
cleanly even if plan 002's PR has not been merged yet.

If plan 002 has already been merged and its branch deleted, owl automatically
falls back to `main` — no manual intervention needed.

## Goal

Extend `utils/text.py`'s `truncate` function to accept a `word_boundary: bool`
parameter (default `False`). When `True`, the truncation point is moved back to
the last space before the cut, so the output never ends mid-word.

Example behavior:

```python
truncate("hello world foo bar", 12, word_boundary=True)
# -> "hello world..."   (cut at space before "foo", not mid-word)

truncate("hello world foo bar", 12, word_boundary=False)
# -> "hello world ..."  (default: cut at exactly max_length - len(suffix))
```

Add tests in `tests/test_text.py` covering:

- `word_boundary=True` trims to the last whole word
- `word_boundary=False` (default) is unchanged from plan 002's behaviour
- Input with no spaces truncates normally even with `word_boundary=True`

## Verification

- `utils/text.py` has the updated `truncate` signature with `word_boundary`
- `tests/test_text.py` has the new test cases
- All tests pass: `python -m pytest tests/test_text.py -v`
