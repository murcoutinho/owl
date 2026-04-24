---
review-rounds: 2
---

# WhatsApp import: resilient per-day consolidation

## Context

WhatsApp import job 2 failed after successfully creating 13 memories because the LLM called `caption_media` and `extract_memories` in the same turn on one particular day. The `ConsolidationToolError` propagated up to `process_whatsapp_import`, which flipped the entire job to `failed` — losing all remaining days that hadn't been processed yet.

Error: `Consolidation must call one tool per turn (got 2); caption_media and extract_memories must happen on separate turns`

This is a transient LLM behavior issue, not a bug in the data. The fix is twofold:

1. The orchestrator should catch per-day consolidation errors and **skip that day** instead of aborting the entire import. Log the error, continue to the next day. At the end, if any days were skipped, set status to `done` (not `failed`) but include a note about skipped days.

2. The multi-tool-per-turn guard in `consolidation.py` should **retry once** instead of crashing immediately. The model occasionally batches tools in one turn; a single retry with the error fed back usually fixes it.

## Working directory

Server at `/Users/lanabarreto/Documents/Murilo/saudade`.

## Existing files to anchor on

- `pipeline/whatsapp_import/orchestrator.py` — `_run_import()` around line 196 has the `for date_str, day_msgs in iter_days(messages)` loop. Calls `_process_day()` which calls `consolidate_day()`. No try/except around `_process_day()` — any exception kills the loop.
- `pipeline/consolidation.py` — around line 130, the `len(tool_calls) > 1` guard raises `ConsolidationToolError` immediately.
- `pipeline/whatsapp_import/orchestrator.py` — `process_whatsapp_import()` around line 92 catches any exception from `_run_import()` and flips the job to `failed`.

## What to change

### 1. Wrap `_process_day` in try/except inside the daily loop

In `_run_import()`, wrap the `_process_day()` call:

```python
try:
    await _process_day(...)
    days_consolidated += 1
except Exception:
    logger.exception(
        "WhatsApp import job %d: consolidation failed for %s — skipping day",
        job.id, date_str,
    )
    skipped_days += 1
    await session.rollback()
    # Re-fetch job after rollback
    job = await session.get(WhatsAppImportJob, job.id)
    continue
```

Track `skipped_days`. At the end, if `skipped_days > 0`, append to `job.error_message` something like `"N days skipped due to consolidation errors"` but still set status to `done`.

### 2. Retry on multi-tool-per-turn in consolidation.py

In the agent loop, when `len(tool_calls) > 1`, instead of raising immediately:
- Feed the error back to the model as a tool response: `"Error: you must call one tool per turn. Please call only extract_memories."`
- Let the loop continue for one more turn
- If the model does it again on the next turn, then raise `ConsolidationToolError`

This turns a hard crash into a recoverable situation for a common LLM misbehavior.

## What does NOT change

- The multi-tool guard is NOT removed — it still enforces one-tool-per-turn, just with a retry
- The `extract_memories` / `caption_media` tool contract stays the same
- Other orchestrator logic (face matching, cleanup, etc.) is untouched
- The status endpoint response shape doesn't change

## Files to modify

- `pipeline/whatsapp_import/orchestrator.py` — wrap `_process_day` in try/except, track skipped days
- `pipeline/consolidation.py` — add retry logic for multi-tool turns

## Verification

- All existing tests pass
- New test: mock `consolidate_day` to raise `ConsolidationToolError` on one day → orchestrator skips it, processes remaining days, job ends as `done` with error note
- New test: consolidation agent loop with multi-tool response → retries once, succeeds on second turn
- Existing consolidation tests still enforce the multi-tool guard (it raises on second offense)

## Risks and known limitations

- Skipping a day means that day's memories are lost. This is acceptable — the user can re-import later, and partial results are better than zero results.
- The retry adds one extra turn to the agent loop budget. Since `_MAX_TURNS = 4`, this is fine.
- If the LLM consistently makes multi-tool calls (model regression), every day would burn one extra turn. The prompt's caption discipline rule should prevent this for WhatsApp imports.
