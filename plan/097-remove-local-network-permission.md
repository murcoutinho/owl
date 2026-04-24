---
review-rounds: 1
priority: low
---

# Remove local network discovery permission prompt

## Context

The app prompts users to "allow finding devices on the local network" on iOS. Saudade does not need local network access — it only communicates with its own backend over HTTPS. This permission prompt confuses users and is unnecessary.

The prompt is likely triggered by a dependency (Expo dev tools, React Native Metro bundler, or a library that uses Bonjour/mDNS). In release builds this should not appear, but if it does, the fix is to ensure no code path triggers the `NSLocalNetworkUsageDescription` entitlement or Bonjour service discovery.

## Working directory

Mobile at `/Users/lanabarreto/Documents/Murilo/saudade-mobile`.

## Existing files to anchor on

- `ios/Saudade/Info.plist` — check for `NSLocalNetworkUsageDescription` or `NSBonjourServices` keys
- `ios/SaudadeShare/Info.plist` — same check for the share extension
- `app.json` — Expo config, check for any network-related plugins
- `package.json` — check dependencies that might register Bonjour services

## What to change

1. Search for and remove any `NSLocalNetworkUsageDescription` key from Info.plist files
2. Search for and remove any `NSBonjourServices` entries
3. Check if any Expo plugin or native module registers for local network discovery and disable it
4. If the prompt comes from a dev-only tool (Metro, Flipper, etc.), ensure it's stripped from release builds via build configuration
5. If a dependency requires it, check if there's a config flag to disable network discovery

## What does NOT change

- HTTPS communication with the backend — unaffected
- Share extension functionality — unaffected
- Any other permissions the app legitimately needs

## Files to modify

- `ios/Saudade/Info.plist` — remove network discovery keys if present
- Possibly `app.json` or Expo plugins — disable network discovery config
- Possibly native modules that register Bonjour services

## Verification

- Build and run on a physical iOS device
- The "find and connect to devices on your local network" prompt should NOT appear
- App should function normally (API calls, media upload, share extension)

## Risks and known limitations

- If the prompt comes from a dependency we can't control, we may need to accept it or file an issue upstream
- Debug builds may still show the prompt if Metro bundler needs it — that's acceptable, only release matters
