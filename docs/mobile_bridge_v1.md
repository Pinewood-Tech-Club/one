# Mobile Bridge v1 Contract

This document defines the web-to-native contract used by `/mobile/onboarding`.

## Namespace
`window.mobileBridge.v1`

## Methods
- `startSchoologyOAuth(): void | Promise<void>`
- `openExternalURL(url: string): void | Promise<void>`
- `onboardingComplete(): void | Promise<void>`
- `getContext(): { platform?: string; appVersion?: string; buildNumber?: string; bridgeVersion?: string }`

## Behavioral Contract
- If `window.mobileBridge.v1` is unavailable, web code must fall back to existing browser behavior.
- If a bridge method throws, web code must surface a user-visible error and avoid silent no-op behavior.
- `onboardingComplete()` indicates native shell should close web onboarding and continue app flow.
- `getContext()` is optional metadata and must never gate onboarding behavior.

## Compatibility
- This is a versioned contract (`v1`).
- Additive fields are allowed.
- Breaking changes require a new namespace version (`v2`).
