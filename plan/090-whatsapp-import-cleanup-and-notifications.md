---
review-rounds: 1
base-branch: owl/089-whatsapp-import-face-matching
---

# WhatsApp import: orphan cleanup and push notifications

## Context

Final step of the WhatsApp import pipeline. After processing completes (plan 088) and face matching is wired (plan 089), we need to:
1. Clean up orphaned MediaAttachments not linked to any memory
2. Clean up staging area files in MinIO
3. Send a push notification to the user

**Before starting, read `docs/whatsapp-import-flow.md` on the branch** — it contains the complete flow design with a flowchart, filter logic documentation, and key design decisions. This plan covers steps 8–9 of that flow.

## Working directory

Server at `/Users/lanabarreto/Documents/Murilo/saudade`.

## Existing files to anchor on

- `pipeline/whatsapp_import/orchestrator.py` (from plan 088) — add cleanup step after processing loop
- `db/models.py` — `MediaAttachment` has no `source` field yet. Need to identify import-created attachments.
- Check existing push notification patterns in the codebase (if any).

## What to change

### 1. Tag import-created MediaAttachments

Add a way to identify MediaAttachments created during import. Options:
- Add `source` column to `MediaAttachment` (simple, queryable)
- Or link them to the `WhatsAppImportJob` via a foreign key

Use whichever pattern is simpler. The cleanup query needs to find "all MediaAttachments from this import job that are not linked to any Memory."

### 2. Cleanup function

```python
async def cleanup_import_orphans(session: AsyncSession, job: WhatsAppImportJob) -> int:
    """Delete MediaAttachments from this import that aren't linked to memories.
    Also delete their MinIO objects and the staging area.
    Returns count of deleted attachments."""
```

- Query MediaAttachments for this job
- Anti-join against memory linkage
- Delete MinIO objects for orphans
- Delete the staging area prefix
- Keep the original zip for re-processing

### 3. Push notification

After cleanup, notify the user that import is complete. Use whatever notification mechanism exists in the app (check for existing patterns — FCM, APNs, or in-app notification table).

### 4. Wire cleanup into orchestrator

Call cleanup after the processing loop completes successfully.

## What does NOT change

- Consolidation, filters, parser — all untouched.
- Existing MediaAttachment behavior for non-import media.

## Files to modify

- `pipeline/whatsapp_import/orchestrator.py` — add cleanup call
- `pipeline/whatsapp_import/cleanup.py` — new file
- `db/models.py` — possibly add source/job linkage to MediaAttachment
- `alembic/versions/` — migration if schema changes needed

## Verification

- All existing tests pass
- New tests: orphan cleanup deletes correct attachments, keeps linked ones
- Staging area deleted after cleanup
- Original zip preserved

## Risks and known limitations

- Small scope, low risk. Cleanup is a best-effort operation — if it fails, orphans remain but don't break anything.
- Push notification depends on existing infrastructure. If none exists, skip and just log.
