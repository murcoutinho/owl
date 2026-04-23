---
review-rounds: 2
---

# iOS share extension: save locally instead of uploading

## Context

The iOS share extension (`ShareViewController.swift`) currently uploads files directly to the server while the share sheet is open. For large files (videos, WhatsApp export zips), this freezes the phone for minutes because the extension blocks on network I/O.

The fix: the extension should only **save the file to shared app storage** and dismiss immediately. The main app picks up pending files on next launch and handles the upload in the background.

This same pattern will be used for WhatsApp export imports — the user shares the zip from WhatsApp to Saudade, the extension saves it locally, and the main app processes it.

Android already follows this pattern via the share intent → `share-receive.tsx` flow.

## Working directory

Mobile at `/Users/lanabarreto/Documents/Murilo/saudade-mobile`.

## Existing files to anchor on

- `ios/SaudadeShare/ShareViewController.swift` — the current share extension. Around line 55, `didSelectPost()` calls `uploadWithRetry()` which blocks on network. Around line 225, `uploadSharedItems()` iterates attachments and uploads each one.
- `lib/shareIntent.ts` — around line 10, the `ShareIntentModule` native module with `getPendingShareData()` and `clearPendingShareData()`. Currently used only on Android.
- `app/share-receive.tsx` — the main app screen that handles shared content. Shows preview, user taps "Enviar", then `api.sendChatImage/Video/Audio/Text` uploads. Around line 36.
- `app/_layout.tsx` — around line 90, detects pending share data on resume and routes to `share-receive`.

## What to change

### 1. Rewrite `ShareViewController.swift` — save only, no upload

Replace `didSelectPost()` to:
1. Resolve attachments (keep existing `resolveAttachment` logic — it's fast, just reads the file)
2. Copy each file to the **App Group shared container** (not NSTemporaryDirectory — that's extension-only)
3. Write a small JSON manifest alongside the files: `{type, uri, mimeType, text}` — same shape as `ShareData` in `shareIntent.ts`
4. Dismiss immediately with `extensionContext?.completeRequest()`

Remove all upload/network code from the extension: `uploadWithRetry`, `uploadSharedItems`, `uploadAttachment`, `uploadText`, `buildMultipartBody*`, `login`, `promptForLogin`. Remove the progress overlay UI — it's no longer needed since the operation is instant.

Keep: `resolveAttachment`, `detectAttachmentKind`, `loadFileURL`, `persistTempFile` (but save to shared container instead of temp), keychain code (still needed to check auth state so the main app can show login if needed).

### 2. App Group shared container

The extension and main app need to share a file system location. Use an App Group container:
- App Group identifier: `group.com.murcoutinho.saudade` (check if already configured in Xcode)
- Extension writes to: `FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: "group.com.murcoutinho.saudade")!/pending-share/`
- Main app reads from the same path

### 3. Update `ShareIntentModule` (iOS native module) to read from App Group

Currently this module only works on Android. Add iOS implementation:
- `getPendingShareData()` — read the JSON manifest from the App Group shared container
- `clearPendingShareData()` — delete the manifest and associated files

### 4. Handle `.zip` files in the extension

Currently the extension only handles image/video/audio (`detectAttachmentKind`). Add support for `.zip` UTType:
- Detect `UTType.zip` or `UTType.archive` → kind = "archive"
- Save zip to shared container same as other files
- `ShareData` gets a new type: `"archive"`

### 5. Update `share-receive.tsx` to handle archives

When `shareData.type === "archive"`:
- Show "WhatsApp export detected" with subject picker
- On confirm, call the WhatsApp import upload endpoint instead of the regular chat endpoints
- This connects to the backend flow from `docs/whatsapp-import-flow.md`

## What does NOT change

- Android share intent flow — already works correctly
- Backend upload endpoints — main app still calls the same APIs
- The main app's upload logic in `share-receive.tsx` for image/video/audio — stays the same, just triggered from the main app instead of the extension
- Server-side processing

## Files to modify

**iOS native:**
- `ios/SaudadeShare/ShareViewController.swift` — rewrite to save-only
- `ios/SaudadeShare/Info.plist` — may need App Group entitlement
- `ios/Saudade/Saudade.entitlements` — add App Group
- `ios/SaudadeShare/SaudadeShare.entitlements` — add App Group

**Expo/React Native:**
- `plugins/share-extension/` or native module — add iOS `getPendingShareData`/`clearPendingShareData`
- `lib/shareIntent.ts` — should work cross-platform after native module update
- `app/share-receive.tsx` — add archive/zip handling with subject picker

## Verification

1. Share an image from Photos to Saudade → extension dismisses immediately (< 1 second) → open Saudade → share-receive screen shows the image → tap send → uploads correctly
2. Share a video from WhatsApp to Saudade → same instant dismiss → open app → video preview → send → uploads
3. Share a WhatsApp export zip → instant dismiss → open app → "WhatsApp export detected" → subject picker → upload starts
4. Share while logged out → extension still saves locally → app shows login then share-receive
5. Large video (100MB+) → extension still dismisses instantly, no freeze

## Risks and known limitations

- App Group setup requires Xcode project changes (entitlements, capabilities). This may need manual Xcode configuration if Expo/EAS doesn't handle it automatically.
- If the user never opens the main app after sharing, files accumulate in the shared container. Need a cleanup policy (delete files older than 24h on app launch).
- The user loses the immediate "sent!" feedback from the current extension. Trade-off: reliability vs immediacy.
- If multiple items are shared before the app is opened, only the latest might be processed. Need to handle a queue of pending shares.
