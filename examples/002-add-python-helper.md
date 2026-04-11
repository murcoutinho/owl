<!-- This is a sample plan. Copy it to `plan/` to try it against your own target repo.
     Make sure `OWL_TARGET_REPOS` points at a repo you're happy to experiment in. -->

# Add Python helper utility

## Context

We need a small reusable utility function for truncating strings with an
ellipsis. This is a typical "add a feature with tests" plan that shows the
shape of normal owl feature work.

## Goal

Create `utils/text.py` with the following function:

```python
def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Return text truncated to max_length characters, appending suffix if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
```

Create `tests/test_text.py` with tests covering:

- Text shorter than `max_length` is returned unchanged
- Text exactly `max_length` is returned unchanged
- Text longer than `max_length` is truncated and the suffix is appended
- Custom `suffix` argument works correctly
- Edge case: `max_length` equal to `len(suffix)` returns only the suffix

## Verification

- `utils/text.py` exists with the `truncate` function
- `tests/test_text.py` exists with at least 5 test cases
- All tests pass: `python -m pytest tests/test_text.py -v`
