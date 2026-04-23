---
review-rounds: 2
base-branch: feat/whatsapp-import
---

# WhatsApp import: job model and upload endpoint

## Context

The WhatsApp import pipeline has parser, filters, transform, and consolidation integration on branch `feat/whatsapp-import`. The next step is infrastructure: a DB model to track import jobs and an API endpoint to receive the WhatsApp export zip, unzip it, and store files in MinIO staging.

**Before starting, read `docs/whatsapp-import-flow.md` on the branch** — it contains the complete flow design with a flowchart, filter logic documentation, and key design decisions. This plan covers steps 1–4 of that flow.

## Working directory

Server at `/Users/lanabarreto/Documents/Murilo/saudade`.

## Existing files to anchor on

- `db/models.py` — all DB models. `MediaAttachment` class around line 392. `Memory` class around line 279. `Subject` around line 76.
- `config.py` — MinIO config around line 51 (`MINIO_ENDPOINT`, `MINIO_BUCKET`).
- `api/` — existing API routers. Check router patterns for auth, file upload, etc.
- `pipeline/whatsapp_import/` — the existing import module (parser, enrich, filters, transform).

## What to change

### 1. New model: `WhatsAppImportJob` in `db/models.py`

Add a new table to track import state:

```python
class WhatsAppImportJob(Base):
    __tablename__ = "whatsapp_import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    # statuses: uploaded → face_matching → processing → done → failed
    staging_prefix: Mapped[str] = mapped_column(String(500), nullable=False)
    # MinIO prefix where raw files are stored: whatsapp-imports/{user_id}/{job_id}/
    chat_text_key: Mapped[str] = mapped_column(String(500), nullable=True)
    # MinIO key for the _chat.txt file
    original_zip_key: Mapped[str] = mapped_column(String(500), nullable=True)
    # MinIO key for the original zip (for re-processing)
    media_count: Mapped[int] = mapped_column(default=0)
    days_kept: Mapped[int] = mapped_column(default=0)
    memories_created: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

### 2. Alembic migration

Create a migration for the new table.

### 3. Upload endpoint in `api/router_whatsapp_import.py`

New router with a POST endpoint:

- `POST /api/whatsapp-import/upload` — multipart form upload
  - Accepts: zip file + `subject_id`
  - Auth required (JWT)
  - Validates subject belongs to user
  - Stores original zip in MinIO at `whatsapp-imports/{user_id}/{job_id}/original.zip`
  - Unzips to `whatsapp-imports/{user_id}/{job_id}/media/`
  - Stores `_chat.txt` separately at `whatsapp-imports/{user_id}/{job_id}/chat.txt`
  - Creates `WhatsAppImportJob` record
  - Returns job ID + status

- `GET /api/whatsapp-import/status/{job_id}` — check job status

### 4. Register the router in `main.py` or `server.py`

## What does NOT change

- No changes to existing models, consolidation, little_bird, or scheduler.
- No background job execution yet (that's the next plan).
- No face matching or day processing — just upload and storage.

## Files to modify

- `db/models.py` — add `WhatsAppImportJob`
- `alembic/versions/` — new migration
- `api/router_whatsapp_import.py` — new file
- `main.py` or `server.py` — register new router

## Verification

- `alembic upgrade head` succeeds
- `pytest tests/` — all existing tests pass
- New tests: upload endpoint returns job ID, zip is unzipped in MinIO, _chat.txt extracted, status endpoint works, auth required

## Risks and known limitations

- Large zip files may timeout on upload. For test users this is acceptable. Chunked upload is out of scope.
- MinIO staging convention (`whatsapp-imports/`) is new — needs to coexist with existing `saudade-media` bucket usage.
