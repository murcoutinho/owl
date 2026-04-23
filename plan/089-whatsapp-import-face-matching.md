---
review-rounds: 2
base-branch: owl/088-whatsapp-import-day-processing-and-memory-persistence
---

# WhatsApp import: face matching via little bird

## Context

The import orchestrator (plan 088) processes days and creates memories, but without face matching the filters rely solely on name/keyword matching. This plan adds face matching: run little bird's `detect_and_match_faces` on each WhatsApp media file using existing face groups (indexed during onboarding), then feed results into the enrichment step.

**Before starting, read `docs/whatsapp-import-flow.md` on the branch** — it contains the complete flow design with a flowchart, filter logic documentation, and key design decisions. This plan covers step 5 of that flow. Face groups already exist from onboarding — the import just searches against them.

Note: this creates a known tech debt of double face detection (once here for filtering, once later via little bird on MediaAttachment rows). See `TODO/whatsapp-import-double-face-detection.md`.

## Working directory

Server at `/Users/lanabarreto/Documents/Murilo/saudade`.

## Existing files to anchor on

- `pipeline/little_bird.py` — `detect_and_match_faces()` around line 200+. Also `_rekognition_client()` for boto3 client setup.
- `pipeline/whatsapp_import/orchestrator.py` (from plan 088) — where face matching needs to be called before enrichment.
- `pipeline/whatsapp_import/enrich.py` — currently reads from `clusters.json`. Needs to accept face match results from little bird instead.
- `pipeline/whatsapp_import/filters.py` — `filter_day_relevance()` and `filter_irrelevant_media()` consume `face_clusters` and `mother_cluster`.
- `db/models.py` — `Subject`, `KnownPerson` for looking up face group collections.

## What to change

### 1. Face matching step in orchestrator

Before parsing/enriching, iterate through all media files in the MinIO staging area:

```python
async def match_faces_for_import(
    session: AsyncSession,
    job: WhatsAppImportJob,
    subject: Subject,
) -> dict[str, list[str]]:
    """Run search_faces_by_image on each media file against subject + known people collections.
    
    Returns: filename -> list of matched person UUIDs/names
    """
```

- For each photo/video in staging: download from MinIO, call Rekognition `search_faces_by_image` against the subject's collection
- For videos: extract frames first (reuse `media_captioning/service.py` scene detection)
- Map results back to filenames
- Update job status to `face_matching` during this phase

### 2. Update enrichment to accept face match results

`enrich.py` currently reads from a `clusters.json` file. For the real integration, it needs to accept face match results as a dict (filename → matched person IDs) instead. The enrichment format stays the same in the message dicts — `face_clusters` field — but the source changes from a file to live match results.

### 3. Identify mother cluster

From `known_people` with relation "mãe", look up the person's face group collection ID and pass as `mother_cluster` to the filters.

## What does NOT change

- Little bird's core detection/matching logic — we call it, not modify it.
- The filter logic — it already handles face clusters correctly.
- Consolidation — unaffected.

## Files to modify

- `pipeline/whatsapp_import/orchestrator.py` — add face matching phase
- `pipeline/whatsapp_import/enrich.py` — accept dict input alongside file input
- `pipeline/whatsapp_import/face_matching.py` — new file with matching logic

## Verification

- All existing tests pass
- New tests: mock Rekognition responses, verify face match results enrich messages correctly
- Integration: face matching results flow through filters correctly (subject photos kept, random photos dropped)

## Risks and known limitations

- Rekognition API cost: ~1 call per media file. For 400 photos + 100 video frames = ~500 calls.
- Double detection cost documented in TODO.
- If subject has no face group (no onboarding photos), face matching is skipped entirely and filters fall back to name/keyword only.
- Video frame extraction adds processing time.
