---
review-rounds: 2
base-branch: owl/091-ios-share-extension-save-locally
---

# Mobile: WhatsApp import screen and API integration

## Context

After plan 091, the iOS share extension saves files locally and dismisses instantly. When the user shares a WhatsApp export zip, the main app needs a screen to:
1. Detect the zip is a WhatsApp export
2. Let the user pick which subject it's for
3. Upload the zip to the backend's WhatsApp import endpoint
4. Show processing status
5. Notify when memories are ready

The backend already has the upload endpoint (`POST /api/whatsapp-import/upload`) and status endpoint (`GET /api/whatsapp-import/status/{job_id}`) on the `feat/whatsapp-import` branch (merged or about to merge to main).

**Before starting, read `docs/whatsapp-import-flow.md` in the saudade server repo** — it contains the full flow design. This plan covers the mobile side of steps 1–3 and 9–10.

## Working directory

Mobile at `/Users/lanabarreto/Documents/Murilo/saudade-mobile`. Server at `/Users/lanabarreto/Documents/Murilo/saudade` (read-only, for API reference).

## Existing files to anchor on

- `app/share-receive.tsx` — around line 19. Handles shared content from the share extension/intent. Currently handles image, video, audio, text. Needs to add "archive" type for zip files.
- `lib/shareIntent.ts` — around line 10. `getPendingShareData()` returns `ShareData` with `{type, uri, mimeType, text}`. Plan 091 adds `type: "archive"` support.
- `lib/api.ts` — around line 346. API client class. Needs new methods for WhatsApp import upload and status polling.
- `contexts/SubjectContext.tsx` — around line 5. Provides `subjects: Subject[]` and `currentSubject`. Used for the subject picker.
- `app/_layout.tsx` — around line 90. Detects pending share data on resume and routes to `share-receive`.

**Server endpoints (from `api/router_whatsapp_import.py`):**
- `POST /api/whatsapp-import/upload` — multipart form: `file` (zip) + `subject_id` (int). Returns `{job_id, status}`.
- `GET /api/whatsapp-import/status/{job_id}` — returns `{id, status, media_count, days_kept, memories_created, error_message}`.

## What to change

### 1. Add WhatsApp import API methods in `lib/api.ts`

```typescript
async uploadWhatsAppExport(zipUri: string, subjectId: number): Promise<{ job_id: number; status: string }> {
  // multipart upload: file + subject_id
}

async getWhatsAppImportStatus(jobId: number): Promise<WhatsAppImportStatus> {
  // GET /api/whatsapp-import/status/{jobId}
}
```

### 2. Update `share-receive.tsx` to handle archive type

When `shareData.type === "archive"`:
- Don't show the regular "Enviar para o Saudade" flow
- Instead show a WhatsApp import flow:
  - Title: "Importar conversa do WhatsApp"
  - Subject picker (list from `useSubject().subjects`)
  - "Iniciar importação" button
  - On tap: call `api.uploadWhatsAppExport(shareData.uri, selectedSubjectId)`
  - Transition to status screen

### 3. New screen or inline status view for import progress

After upload starts, show:
- "Processando sua conversa do WhatsApp..."
- Status updates: uploading → matching faces → creating memories → done
- Poll `api.getWhatsAppImportStatus(jobId)` every few seconds
- When done: "X memórias criadas! Ir para o diário"
- On error: show error message + retry option

This could be:
- A new route `app/whatsapp-import-status.tsx`
- Or inline within `share-receive.tsx` using state

Prefer inline to avoid route complexity — the share-receive screen already has status states.

### 4. Subject picker component

Simple list of subjects with radio selection:
- Show subject name + relationship
- Pre-select `currentSubject` if only one exists
- If only one subject, skip picker entirely and go straight to upload

Can be a simple `FlatList` within the share-receive screen — no need for a separate component if the list is small (most users have 1-3 subjects).

## What does NOT change

- Backend WhatsApp import pipeline — fully built, no changes needed
- Android share intent flow — plan 091 handles the extension side
- Regular image/video/audio sharing — stays the same
- Other app screens

## Files to modify

- `lib/api.ts` — add upload + status methods
- `app/share-receive.tsx` — add archive handling, subject picker, status polling
- Possibly `lib/types.ts` — add `WhatsAppImportStatus` type

## Verification

1. Share a WhatsApp export zip to Saudade → extension dismisses → open app → see "Importar conversa do WhatsApp" with subject picker
2. Pick subject → tap "Iniciar" → upload starts → progress shown → "X memórias criadas"
3. With multiple subjects: picker shows all, correct one selected
4. With one subject: picker skipped, goes directly to upload
5. Network error during upload: error message + retry button
6. Close app during processing → reopen → status screen resumes polling (or shows "check status" if job was started)

## Risks and known limitations

- Large zip upload may take minutes on slow connections. Need upload progress indicator (not just a spinner).
- If the app is killed during upload, the job is lost. Could save job_id to AsyncStorage for resume.
- Polling interval: too fast wastes battery, too slow feels unresponsive. Start with 5-second intervals.
- The backend processing can take several minutes for large exports (69 days × 1 LLM call each). User needs clear "this will take a while" messaging.
