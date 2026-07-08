# Security Policy

Pinewood One handles **sensitive student data** — Schoology grades, schedules,
and assignments — as well as **OAuth tokens** and session credentials. We take
security reports seriously and appreciate responsible disclosure.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.** Public
issues disclose the vulnerability before it can be fixed.

**Primary channel — GitHub private security advisory:**

> https://github.com/Pinewood-Tech-Club/one/security/advisories/new

Use the "Report a vulnerability" button on that page to open a private advisory.
This keeps the report confidential and lets us collaborate on a fix.

If you cannot use the advisory workflow, contact a maintainer
(**@AdamEXu**) privately through GitHub and ask for a secure channel. Please do
not post vulnerability details in public comments, discussions, or issues.

### What to include

- A description of the vulnerability and its impact.
- Steps to reproduce (proof of concept if possible).
- Affected component(s) and any relevant version/commit.
- Any suggested remediation, if you have one.

## Scope

We are especially interested in reports affecting:

- **Authentication & sessions** — Google OAuth login, session issuance,
  expiration, and cookie handling.
- **OAuth token storage** — encryption at rest (Fernet), token handling, and
  any path where tokens could leak (logs, error responses).
- **Scraper** (`backend/services/scraper/`) — server-side request forgery
  (SSRF) via scraped URLs, and job-lease integrity / race conditions.
- **Chat SSE** — the server-sent-events chat stream (authorization, data
  leakage across users).
- **IDOR / broken access control** — any endpoint returning user data without
  verifying ownership.

## Please avoid

To protect real users while testing, please **do not**:

- Scan, probe, or attack the **production Cloudflare tunnel** or any live
  deployment.
- Access, exfiltrate, or modify **real student data** or another person's
  account.
- Run automated scanners against production infrastructure.

Test against a local development instance whenever possible. If you believe a
finding can only be demonstrated against production, describe it in the private
advisory and we will help reproduce it safely.

## Our commitment

- We will acknowledge your report as soon as we can.
- We will keep you informed as we investigate and remediate.
- We will credit reporters who wish to be acknowledged, once a fix is released.

Thank you for helping keep Pinewood One and its users safe.
