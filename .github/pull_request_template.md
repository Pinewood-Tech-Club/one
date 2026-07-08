<!--
Thanks for contributing to Pinewood One! Please fill out this template.
PRs with an incomplete security checklist will not be merged.
-->

## What & why

<!-- What does this PR change, and why? Link any related issues. -->

## Area

<!-- Check all that apply -->

- [ ] Frontend
- [ ] Backend
- [ ] Scraper
- [ ] Convex
- [ ] Infra / tooling

## How tested

<!-- Describe how you verified this change (manual steps, endpoints hit, etc.). -->

## Security checklist

<!-- Every box must be checked (or marked N/A with a reason) before merge. -->

- [ ] Endpoints returning user data enforce **ownership / authorization** (no IDOR — a user cannot read another user's data).
- [ ] `@auth_required` is applied to routes that need authentication.
- [ ] No **SSRF**: any outbound/scraped URL is validated (scheme + host) before it is fetched.
- [ ] No secrets committed — no `.env`, `*.db`, keys, or credentials in the diff.
- [ ] Flask **debug mode is not forced on** in a production code path.
- [ ] OAuth **tokens stay encrypted** at rest and are **never logged** or returned in responses.

## Checks

- [ ] `cd frontend && pnpm lint` passes.
- [ ] Backend still starts and affected endpoints work (`make dev-backend`).
