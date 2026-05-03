# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Flask + PostgreSQL REST API for recording and analyzing LoadRunner-style performance test data. It supports distributed tracing via correlation IDs that link transactions and async messages across test runs.

## Commands

**Development (hot reload, DB port exposed):**
```bash
docker compose -f docker-compose.dev.yml up
# App: http://localhost:8000, DB: localhost:5432
```

**Production:**
```bash
docker compose up
```

**Run migrations:**
```bash
python -m app.db.migrate            # Apply pending migrations
python -m app.db.migrate --status   # Show status
```

**Type checking and linting:**
```bash
mypy app          # strict type checking (mypy --strict equivalent)
ruff check .      # lint + annotation enforcement
ruff check --fix . # auto-fix safe violations
```

**Tests (requires running Postgres):**
```bash
docker compose -f docker-compose.dev.yml up db -d
TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test pytest -v
# Single test:
TEST_DATABASE_URL=... pytest -v tests/test_api.py::test_name
```

## Architecture

**Entity hierarchy:**
```
Projects → Tests → Test Runs → Transactions → Transaction Steps
                             → Messages (fire-and-forget)
Correlation Links (n-to-n join: transactions ↔ messages by correlation_id)
```

**Run state machine:** `pending → running → passed/failed/aborted`  
Transitions enforced with `UPDATE ... WHERE status = 'pending'` — not application-level logic.

**Blueprints in `app/routes/`:** `projects`, `tests`, `test_runs`, `transactions`, `messages` — each maps to `/api/<resource>/` endpoints.

**No ORM:** Raw parameterized SQL (`%s` placeholders) via psycopg3. Connection pooling in `app/db/connection.py` via `get_conn()` context manager (commits on success, rolls back on exception).

**JSONB fields** (`config`, `run_metadata`, `extra`, `payload`) allow schema-free extension without migrations.

**Migrations** in `migrations/V###__description.sql` are verified with SHA256 checksums — never modify an applied migration.

## Response conventions

All responses use `{"data": ...}` on success or `{"error": "message"}` on failure. Helpers `ok()`, `created()`, `error()`, `not_found()` in `app/utils.py` enforce this. Paginated list endpoints include `items`, `total`, `pages`, `per_page`.

## Testing conventions

- Real Postgres required — schema uses Postgres enums and features incompatible with SQLite.
- `conftest.py` provides `app` (session-scoped, runs migrations), `client`, and `clean_db` (autouse, truncates tables between tests).
- Use helper functions `make_project`, `make_test`, `make_run` to build test data.

## Key environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Full DSN (built automatically by Docker Compose) |
| `TEST_DATABASE_URL` | Separate DB for pytest |
| `DB_POOL_MIN` / `DB_POOL_MAX` | Connection pool bounds (default 2/10) |
| `SECRET_KEY` | Flask secret |
| `APP_PORT` | Server port (default 8000) |
