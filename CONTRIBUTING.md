# Contributing to Pinewood One

Thanks for your interest in contributing! Pinewood One is a full-stack app that
integrates Schoology data (grades, schedules, assignments) with a Next.js
frontend and a Flask backend. Because it handles sensitive student data and
OAuth tokens, we hold contributions to a high security bar. Please read this
guide before opening a pull request.

## Getting set up

The repository ships a `Makefile` that wraps the dev tooling. From the repo root:

```bash
make setup          # one-time: install deps and prepare the dev environment
make init           # initialize local config / databases
make doctor         # verify your environment is healthy
make secret-export  # export the local secrets needed for development
```

To run the app during development:

```bash
make dev            # runs doctor, then starts the full stack
make dev-frontend   # frontend only (Next.js)
make dev-backend    # backend only (Flask)
make dev-convex     # Convex dev deployment
```

Ports:

- Frontend: **3112**
- Backend: **3111**

If something looks off, run `make doctor` first — it checks each component
(`--component frontend`, `--component backend`, `--component convex`).

## Workflow

1. **Branch off `main`.** Create a topic branch for your change; do not commit
   directly to `main`.
2. Make your change, keeping it focused and reasonably small.
3. **Lint the frontend** before pushing:
   ```bash
   cd frontend && pnpm lint
   ```
4. If you touch the backend, make sure it still starts (`make dev-backend`) and
   that any affected endpoints behave as expected.
5. Open a pull request against `main` and **fill out the PR template**,
   including the security checklist. PRs with an incomplete security checklist
   will not be merged.

## Security expectations

Every contributor is responsible for not regressing security. When you open a
PR, confirm each item in the security checklist, in particular:

- Endpoints that return user data enforce ownership / authorization (no IDOR)
  and apply the `@auth_required` decorator where authentication is needed.
- Any outbound URL (especially in the scraper) is validated — no SSRF.
- OAuth tokens stay encrypted at rest and are never logged.
- Flask debug mode is never forced on in a production code path.

**Never commit secrets.** Do not commit `.env` files, `*.db` databases, private
keys, or any credential. These paths are already covered by `.gitignore` — keep
them out of your diffs.

## Reporting a vulnerability

Do **not** open a public issue for security problems. Please follow the private
process described in [`.github/SECURITY.md`](.github/SECURITY.md).

## Questions

Open a regular issue using one of the issue templates (bug report or feature
request) for non-security topics.
