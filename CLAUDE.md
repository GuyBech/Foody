# Foody — Project Constraints

These decisions are **locked**. Do not propose alternatives without explicit
user approval.

## 1. Database

- **Supabase** (managed PostgreSQL with pgvector) is the only supported backend.
- Connection string lives in `.env` as `DATABASE_URL`.
- **Do not** suggest local Docker, local Postgres, or SQLite — even for
  tests/dev. Use a Supabase branch if isolation is needed.
- Migrations: `alembic upgrade head` runs directly against the Supabase URL.

## 2. LLM

- All backend Anthropic calls default to the **cheapest currently available**
  Haiku model: `claude-haiku-4-5`.
- `claude-3-5-haiku-*` was retired (EOL 2026-02-19) — do not use it.
- Cost-estimation constants in `agent/*.py` should reflect Haiku 4.5 pricing
  (~$1.00 input / $5.00 output / $0.10 cache-read per MTok).
- Escalate to Sonnet or Opus **only** when explicitly requested by the user.

## 3. Google Calendar Auth

- **Service-account based, stateless.**
- Credentials file: `google_credentials.json` at the repo root (gitignored).
- **No user OAuth flows.** Do not read `UserIntegration.access_token` /
  `refresh_token`. Do not implement an `/oauth/callback` route.
- For the service account to see a calendar, the calendar must be shared
  with the service-account email. Personal Gmail calendars (e.g.
  `…@gmail.com`) require domain-wide delegation — log and skip them
  rather than failing the whole job.
