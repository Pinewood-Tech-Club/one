# Mobile Auth Contract v1 (`/api/mobile/v1/*`)

## Token Model
- Mobile access token: RS256 JWT, `aud=mobile_api`, default TTL `900s`.
- Mobile refresh token: opaque random token (`token_urlsafe(64)`), server stores HMAC-SHA256 hash only.
- Convex token: RS256 JWT, `aud=convex`, TTL `300s` from mobile endpoint.
- One-time auth code: opaque random (`token_urlsafe(32)`), single-use, TTL `120s`.
- Web session ticket: opaque random (`token_urlsafe(32)`), single-use, TTL `60s`.

## OAuth Start + Callback
### `GET /api/mobile/v1/auth/google/start`
Query params:
- `redirect_uri` (required; allowlisted)
- `device_id` (required)
- `code_challenge` (required)
- `code_challenge_method` (required; must be `S256`)
- `state` (optional client state echoed back)

Behavior:
- Creates signed state payload with nonce + PKCE challenge + device info.
- Redirects to Google OAuth.

Errors:
- `400 {"error":"invalid_request"}`

### `GET /api/mobile/v1/auth/google/callback`
Query params from Google:
- `code`, `state`, optional `error`

Behavior:
- Exchanges Google code at backend.
- Validates hosted domain (`pinewood.edu`).
- Creates/updates user.
- Stores one-time auth code in `mobile_auth_codes`.
- Redirects to app callback URI with `code` (or `error`).

## Session Exchange and Refresh
### `POST /api/mobile/v1/auth/exchange`
Body:
```json
{
  "code": "<one-time-code>",
  "code_verifier": "<pkce_verifier>",
  "device_id": "<device-id>",
  "platform": "ios",
  "app_version": "1.0.0",
  "locale": "en-US",
  "timezone": "America/Los_Angeles"
}
```

Success response:
```json
{
  "access_token": "...",
  "expires_in": 900,
  "refresh_token": "...",
  "refresh_expires_in": 2592000,
  "token_type": "Bearer",
  "user": {
    "user_id": 1,
    "email": "student@pinewood.edu",
    "name": "Student"
  }
}
```

Errors:
- `400 {"error":"invalid_grant"}`
- `401 {"error":"unauthorized"}`
- `409 {"error":"device_mismatch"}`

### `POST /api/mobile/v1/auth/refresh`
Body:
```json
{
  "refresh_token": "...",
  "device_id": "..."
}
```

Success shape matches `/auth/exchange`.

Errors:
- `401 {"error":"invalid_token"}`
- `401 {"error":"reuse_detected"}`

Replay handling:
- If revoked-but-unexpired refresh token is reused, active tokens for that `(user_id, device_id)` are revoked.

### `POST /api/mobile/v1/auth/logout`
Auth: bearer mobile access token.
Body:
```json
{
  "refresh_token": "optional",
  "all_devices": false
}
```

Success:
- HTTP `204`

## User + Convex
### `GET /api/mobile/v1/me`
Auth: bearer.

Response:
```json
{
  "user_id": 1,
  "email": "student@pinewood.edu",
  "name": "Student",
  "onboarding_step": "welcome",
  "schoology_connected": false
}
```

### `GET /api/mobile/v1/convex/token`
Auth: bearer.

Response:
```json
{
  "token": "<convex-jwt>",
  "expires_in": 300
}
```

## Web Onboarding Bootstrap
### `POST /api/mobile/v1/web/session-ticket`
Auth: bearer.
Body:
```json
{
  "device_id": "..."
}
```

Response:
```json
{
  "ticket": "...",
  "expires_in": 60
}
```

### `GET /api/mobile/v1/web/session/bootstrap`
Query:
- `ticket`
- `redirect` (must match frontend origin and `/mobile/onboarding*` path)

Success:
- Sets Flask session cookie and redirects.

Errors:
- `400 {"error":"invalid_ticket"}`
- `410 {"error":"expired_ticket"}`
- `400 {"error":"invalid_redirect"}`

## Device Registration
### `POST /api/mobile/v1/devices/register`
Auth: bearer.
Body:
```json
{
  "device_id": "...",
  "platform": "ios",
  "app_version": "1.0.0",
  "push_token": "optional",
  "push_env": "development",
  "locale": "en-US",
  "timezone": "America/Los_Angeles"
}
```

Response:
```json
{"success": true}
```

### `DELETE /api/mobile/v1/devices/register`
Auth: bearer.
Body:
```json
{"device_id": "..."}
```

Success:
- HTTP `204`

## Banner Metadata
### `GET /api/mobile/v1/banner/upcoming`
Response:
```json
{
  "image_url": "https://...",
  "version": "v1",
  "cache_ttl_seconds": 86400
}
```

Headers:
- `Cache-Control: public, max-age=<cache_ttl_seconds>`
