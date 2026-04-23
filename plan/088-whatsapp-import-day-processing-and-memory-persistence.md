---
review-rounds: 2
base-branch: owl/087-whatsapp-import-job-model-and-upload
---

# WhatsApp import: day processing loop and memory persistence

## Context

With the upload endpoint and job model in place (plan 087), this plan wires the core processing loop: parse the chat, enrich with face data, run filters, call `consolidate_day` for each kept day, and persist the resulting memories to the database.

**Before starting, read `docs/whatsapp-import-flow.md` on the branch** — it contains the complete flow design with a flowchart, filter logic documentation, and key design decisions. This plan covers steps 5–7 of that flow. The processing runs as an async background job triggered after upload completes.

## Working directory

Server at `/Users/lanabarreto/Documents/Murilo/saudade`.

## Existing files to anchor on

- `pipeline/whatsapp_import/parser.py` — `parse_messages()`, `iter_days()`
- `pipeline/whatsapp_import/enrich.py` — `enrich_messages()`
- `pipeline/whatsapp_import/filters.py` — `filter_day_relevance()`, `filter_irrelevant_media()`, `infer_parent_clusters()`
- `pipeline/whatsapp_import/transform.py` — `transform_to_buffer_entries()`
- `pipeline/consolidation.py` — `consolidate_day()` around line 43
- `scheduler/daily_batch.py` — memory persistence logic around lines 577–620 (Memory, MemorySubject, MemoryAuthor creation, embedding generation)
- `pipeline/embedding.py` — embedding generation
- `db/models.py` — `WhatsAppImportJob` (from plan 087), `Memory`, `MemorySubject`, `MemoryPerson`, `MediaAttachment`

## What to change

### 1. New file: `pipeline/whatsapp_import/orchestrator.py`

The main processing function:

```python
async def process_whatsapp_import(session: AsyncSession, job_id: int) -> None:
```

This function:
1. Loads the `WhatsAppImportJob` record
2. Updates status to `processing`
3. Downloads `_chat.txt` from MinIO
4. Loads subject, known_people, user data from DB
5. Parses messages, enriches with face match data (if available)
6. Loops through days:
   - Runs filter 1 (day relevance)
   - Runs filter 2 (irrelevant media)
   - Creates `MediaAttachment` rows for surviving media (tagged `source_type="whatsapp_import"`)
   - Transforms to buffer entries
   - Calls `consolidate_day(source_type=WHATSAPP_IMPORT_SOURCE)`
   - Persists memories (reuse logic from `scheduler/daily_batch.py`)
7. Updates job status to `done` with counts
8. On error: updates status to `failed` with error message

### 2. Memory persistence helper

Extract the memory-writing logic from `scheduler/daily_batch.py` (around lines 577–620) into a reusable function that both the scheduler and the import orchestrator can call. This avoids duplicating the Memory/MemorySubject/MemoryAuthor/embedding creation code.

```python
async def persist_consolidation_output(
    session: AsyncSession,
    output: ConsolidationOutput,
    user: User,
    subject: Subject,
    buffer_date: date,
    source_type: str,
    ...
) -> list[Memory]:
```

### 3. Wire to upload endpoint

After upload completes in the API endpoint (plan 087), trigger the processing as a background task.

### 4. Update `WhatsAppImportJob` status transitions

Add status updates at each phase of processing.

## What does NOT change

- The consolidation pipeline itself — no changes to `consolidate_day` or prompts.
- The scheduler's existing consolidation flow — it should use the extracted helper but behavior is identical.
- Little bird — face matching is handled separately (plan 089).

## Files to modify

- `pipeline/whatsapp_import/orchestrator.py` — new file
- `scheduler/daily_batch.py` — extract memory persistence into a shared helper
- `pipeline/whatsapp_import/persistence.py` — new file for the shared helper (or add to existing module)
- `api/router_whatsapp_import.py` — wire background task trigger

## Verification

- All existing tests pass (especially consolidation and scheduler tests)
- New tests: orchestrator processes a mock day, creates Memory + MemorySubject rows, updates job status
- Integration test: end-to-end from parsed messages → memories in DB

## Risks and known limitations

- Extracting memory persistence from `daily_batch.py` touches critical production code. Must be a pure refactor with no behavior change.
- No face matching yet — media enrichment will be empty until plan 089. The pipeline handles this gracefully (filters fall back to name/keyword matching only).
- No push notification yet (plan 090).
