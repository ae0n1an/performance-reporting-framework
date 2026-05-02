# LoadRunner-style Performance Test API

Flask + PostgreSQL (psycopg3), no ORM. Raw SQL with a lightweight migration runner.

## Quick start

```bash
cp .env.example .env

# Dev (hot reload, Postgres exposed on localhost:5432)
docker compose -f docker-compose.dev.yml up

# Prod
docker compose up
```

## Migrations

Migration files live in `migrations/` and are named `V001__description.sql`, `V002__description.sql`, etc.

The migration runner tracks applied versions in a `schema_migrations` table and runs automatically on container startup.

```bash
# Apply pending migrations manually
python -m app.db.migrate

# Check status
python -m app.db.migrate --status
```

To add a new migration, create the next numbered file:
```
migrations/V005__add_tags_to_tests.sql
```

## Running tests

Tests require a real Postgres instance (we use Postgres-specific types — no SQLite).

```bash
# Spin up just the DB
docker compose -f docker-compose.dev.yml up db -d

# Run tests
TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test pytest -v
```

## API

### Projects
| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/` | List (paginated) |
| POST | `/api/projects/` | Create |
| GET | `/api/projects/:id` | Get |
| PATCH | `/api/projects/:id` | Update |
| DELETE | `/api/projects/:id` | Delete |

### Tests
| Method | Path | Description |
|---|---|---|
| GET | `/api/tests/?project_id=` | List |
| POST | `/api/tests/` | Create |
| GET | `/api/tests/:id` | Get |
| PATCH | `/api/tests/:id` | Update |
| DELETE | `/api/tests/:id` | Delete |

### Test Runs
| Method | Path | Description |
|---|---|---|
| GET | `/api/runs/?test_id=&status=` | List |
| POST | `/api/runs/` | Create (status: pending) |
| GET | `/api/runs/:id` | Get |
| POST | `/api/runs/:id/start` | Transition to running |
| POST | `/api/runs/:id/finish` | Transition to passed/failed/aborted |
| DELETE | `/api/runs/:id` | Delete |

### Transactions
| Method | Path | Description |
|---|---|---|
| GET | `/api/transactions/?run_id=&name=&status=` | List |
| POST | `/api/transactions/` | Record one transaction |
| POST | `/api/transactions/bulk` | Batch ingest `{run_id, transactions:[]}` |
| GET | `/api/transactions/:id` | Get (includes steps) |
| POST | `/api/transactions/:id/steps` | Add a sub-step |
| GET | `/api/transactions/by-correlation/:id` | Find by correlation ID |

### Messages (fire-and-forget)
| Method | Path | Description |
|---|---|---|
| GET | `/api/messages/?run_id=&correlation_id=` | List |
| POST | `/api/messages/` | Send (optionally link to a transaction) |
| GET | `/api/messages/:id` | Get |
| PATCH | `/api/messages/:id/status` | Update status (delivered/failed/timeout) |
| POST | `/api/messages/correlate` | Explicitly link tx + message by correlation ID |
| GET | `/api/messages/trace/:correlation_id` | Full end-to-end trace |
