# Performance Reporting Framework

Flask + PostgreSQL REST API for recording and analysing LoadRunner-style performance test data. Supports distributed tracing via correlation IDs that link transactions and async messages across test runs.

No ORM — raw parameterised SQL (psycopg3) with a lightweight migration runner.

## Quick start

```bash
# Dev — hot reload, Postgres exposed on localhost:5432
docker compose -f docker-compose.dev.yml up

# Production
docker compose up
```

App runs on `http://localhost:8000`. The dev compose also exposes Postgres on `localhost:5432`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | set by Docker Compose | Full Postgres DSN |
| `SECRET_KEY` | `dev-secret` | Flask session secret |
| `DB_POOL_MIN` | `2` | Connection pool minimum |
| `DB_POOL_MAX` | `10` | Connection pool maximum |
| `APP_PORT` | `8000` | Server port |

For local development outside Docker, create a `.env` file in the project root:

```
DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner
SECRET_KEY=change-me
```

## Running tests

Tests require a real Postgres instance (the schema uses Postgres enums and JSONB — SQLite is not supported).

```bash
# Start just the database
docker compose -f docker-compose.dev.yml up db -d

# Run the full suite with coverage
TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test pytest -v

# Run a single test
TEST_DATABASE_URL=... pytest -v tests/test_runs.py::test_finish_not_running

# Run without coverage (faster during active development)
TEST_DATABASE_URL=... pytest -v -p no:cov
```

The test database is created automatically on first run if it does not exist. Coverage must reach **80%** or the suite fails. An HTML report is written to `htmlcov/` after each run.

```bash
open htmlcov/index.html
```

## Code quality

```bash
ruff check .          # lint
ruff check --fix .    # lint + auto-fix safe violations
mypy app tests        # strict type checking
```

### Pre-commit hooks

Ruff and mypy run automatically before every commit, blocking it if either fails.

```bash
pip install pre-commit
pre-commit install     # one-time setup per clone
```

To run the hooks manually without committing:

```bash
pre-commit run --all-files
```

## CI

GitHub Actions runs on every push and pull request:

- **lint** — `ruff check` + `mypy` (no database required)
- **test** — full pytest suite with coverage against a real Postgres 16 instance

Pull requests cannot be merged to `main` until both jobs pass. This is enforced via branch protection rules (**Settings → Branches → Require status checks**: `lint`, `test`).

## Migrations

Migration files live in `migrations/` named `V001__description.sql`, `V002__description.sql`, etc. Applied versions are tracked in a `schema_migrations` table with SHA-256 checksums — never modify an applied migration.

```bash
python -m app.db.migrate            # apply pending
python -m app.db.migrate --status   # show applied / pending
```

To add a new migration, create the next numbered file:

```
migrations/V004__add_tags_to_tests.sql
```

## API reference

All responses use `{"data": ...}` on success or `{"error": "message"}` on failure. Paginated list endpoints include `items`, `total`, `page`, `pages`, `per_page`.

### Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/health/` | Returns `{"status": "ok"}` and verifies DB connectivity |

### Projects

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/` | List (paginated) |
| POST | `/api/projects/` | Create — requires `name`, `slug` |
| GET | `/api/projects/:id` | Get |
| PATCH | `/api/projects/:id` | Update `name`, `slug`, `description` |
| DELETE | `/api/projects/:id` | Delete (cascades to tests → runs → transactions) |

### Tests

| Method | Path | Description |
|---|---|---|
| GET | `/api/tests/?project_id=` | List, optionally filtered by project |
| POST | `/api/tests/` | Create — requires `project_id`, `name` |
| GET | `/api/tests/:id` | Get |
| PATCH | `/api/tests/:id` | Update `name`, `description`, `config` |
| DELETE | `/api/tests/:id` | Delete |

### Test runs

Runs follow a strict state machine: `pending → running → passed / failed / aborted`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/runs/?test_id=&status=` | List, optionally filtered |
| POST | `/api/runs/` | Create (status: `pending`) — requires `test_id` |
| GET | `/api/runs/:id` | Get |
| POST | `/api/runs/:id/start` | Transition `pending → running` |
| POST | `/api/runs/:id/finish` | Transition `running → passed / failed / aborted` — requires `status` |
| DELETE | `/api/runs/:id` | Delete |

### Transactions

Transactions record individual measured operations. `kind=message` records async fire-and-forget messages. Correlation IDs link the end boundary of one transaction to the start boundary of another, enabling end-to-end distributed traces.

| Method | Path | Description |
|---|---|---|
| GET | `/api/transactions/?run_id=&name=&status=&kind=&correlation_id=` | List with optional filters |
| POST | `/api/transactions/` | Record one transaction — requires `run_id`, `name`, `start_time` |
| POST | `/api/transactions/bulk` | Batch ingest `{"run_id": "...", "transactions": [...]}` — all items validated before any are inserted |
| GET | `/api/transactions/:id` | Get (includes sub-steps) |
| PATCH | `/api/transactions/:id` | Update `status`, `end_time`, `duration_ms`, `error_message`, correlation IDs, `acknowledged_at` |
| POST | `/api/transactions/:id/steps` | Add a sub-step — requires `name` |
| GET | `/api/transactions/trace/:correlation_id` | All transactions whose start or end boundary carries this correlation ID |

## Data model

```
Projects
  └── Tests
        └── Test Runs
              └── Transactions  (kind: transaction | message)
                    └── Transaction Steps
```

Correlation links are implicit — any transaction sharing a `start_correlation_id` or `end_correlation_id` value is part of the same distributed trace, queryable via `/api/transactions/trace/:correlation_id`.
