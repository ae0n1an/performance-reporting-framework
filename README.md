# Performance Engineering Automation Stack

End-to-end automation for LoadRunner Enterprise (CE 25.x) performance testing:
trigger runs via Azure Pipelines, fetch and parse results, store in InfluxDB +
Postgres, feed Grafana dashboards, and auto-generate Confluence reports.

---

## Repository structure

```
perf-stack/
├── docker-compose.yml                      # InfluxDB + Postgres + Grafana
├── .env.example                            # Copy to .env and fill in secrets
├── postgres/
│   └── init/
│       └── 01_schema.sql                   # Full DB schema (auto-runs on first start)
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── datasources.yml             # InfluxDB + Postgres auto-provisioned
├── lre_fetch_and_parse.py                  # Fetch Results zip from LRE, parse DB → CSV
├── perf_ingest.py                          # Ingest CSV (or Event Hub payload) → InfluxDB + Postgres
├── azure-pipeline-perf.yml                 # Full Azure DevOps pipeline
└── example_event_hub_payload.json          # Schema for non-LRE event sources
```

---

## Architecture

```
Azure DevOps Pipeline
    │
    ├── Trigger LRE scenario (REST API)
    ├── Poll until run completes
    ├── lre_fetch_and_parse.py
    │       ├── GET /Runs/{ID}/Results → find ANALYZED RESULT zip
    │       ├── Download Results_<ID>.zip
    │       ├── Extract .db (SQLite, CE 2023+) or .mdb (older LRE)
    │       ├── Query Event_meter + Event_map
    │       ├── Compute percentiles in Python (numpy)
    │       └── Write lre_results.csv
    │
    └── perf_ingest.py
            ├── Parse CSV → validate data quality
            ├── Write transaction metrics → InfluxDB (Grafana dashboards)
            ├── Write run + transaction rows → Postgres (comparisons, SLA)
            ├── Compute run-over-run deltas
            ├── Evaluate SLA thresholds
            └── Gate pipeline on pass/fail
```

Non-LRE sources (Azure Event Hub, other pipeline jobs) post a JSON payload
directly to `perf_ingest.py generic` — same DB, same Grafana dashboards.

---

## Quick start (local stack)

```bash
cp .env.example .env
# Edit .env — change all passwords

docker compose up -d
# Postgres runs 01_schema.sql automatically on first start
# Grafana provisions both datasources automatically

# Verify:
# Grafana:    http://localhost:3000   (admin / your password)
# InfluxDB:   http://localhost:8086
# Postgres:   psql -h localhost -U perfuser -d perfdb
```

---

## Before running the pipeline

### 1. Verify LRE results DB format (important)

The pipeline assumes your LRE CE 25.x installation writes a SQLite `.db`
file inside the `Results_<ID>.zip` that contains raw per-sample transaction
data in `Event_meter`. This is true for CE 2023+ with SQLite configured,
but **verify before relying on it in production**:

```bash
# Download a completed run's zip manually from the LRE UI
# Then inspect it:
unzip Results_42.zip -d /tmp/lre_check
sqlite3 /tmp/lre_check/Results_42.db

sqlite> SELECT name FROM sqlite_master WHERE type='table';
# Should include: Event_meter, Event_map, TransactionEndStatus, ...

sqlite> SELECT COUNT(*) FROM Event_meter;
# Should return thousands of rows (one per transaction sample per vuser)
# If 0 or table missing — see Troubleshooting below
```

Alternatively, use the `--skip-download` flag to test locally:
```bash
python lre_fetch_and_parse.py --run-id 42 \
    --skip-download /tmp/Results_42.zip \
    --output test.csv
```

### 2. Check your DB mode in LRE Controller

Controller → Tools → Options → Database:
- If **SQLite** is checked → `.db` file, script works as-is
- If **MS Access** is checked → `.mdb` file, add `apt-get install mdbtools`
  to the pipeline's install step

### 3. Add pipeline variables in Azure DevOps

Under Pipelines → Library → Variable Groups, add these as secrets:

| Variable | Example |
|---|---|
| LRE_HOST | https://your-lre-server |
| LRE_USER | admin |
| LRE_PASSWORD | (secret) |
| LRE_DOMAIN | DEFAULT |
| LRE_PROJECT | MyProject |
| INFLUX_URL | http://your-influx-host:8086 |
| INFLUX_TOKEN | (secret) |
| INFLUX_ORG | perf-org |
| INFLUX_BUCKET | perf-results |
| POSTGRES_HOST | your-postgres-host |
| POSTGRES_USER | perfuser |
| POSTGRES_PASSWORD | (secret) |
| POSTGRES_DB | perfdb |

### 4. Register your tests in Postgres

```sql
-- Connect: psql -h localhost -U perfuser -d perfdb

-- Add a test definition
INSERT INTO test_definitions (name, source_tool, environment, application, lre_project)
VALUES ('Checkout flow', 'loadrunner', 'staging', 'ecommerce-api', 'MyLREProject')
RETURNING id;  -- copy this UUID for the pipeline parameter

-- Add SLA thresholds
INSERT INTO sla_definitions (test_definition_id, transaction_name, max_p95_ms, max_error_rate_pct, severity)
VALUES ('<uuid>', '*', 2000, 1.0, 'hard');
```

---

## Running the pipeline

```yaml
# Trigger manually or add as a stage in your existing pipeline:
parameters:
  testDefId:    <uuid from Postgres>
  lreScenarioId: <LRE scenario ID>
  vusers:       100
  failOnSla:    true
```

---

## Ingesting non-LRE results (Event Hub / other pipelines)

Adapt `example_event_hub_payload.json` to match your source schema, then:

```bash
python perf_ingest.py generic \
    --payload  event.json \
    --source   event_hub \
    --test-def-id <uuid>
```

---

## Postgres schema overview

```
test_definitions    — what a test is (name, tool, app, environment)
    │
    ├── test_runs           — one row per execution (status, data_quality, SLA result)
    │       │
    │       └── transaction_results  — one row per transaction per run (all percentiles + deltas)
    │
    ├── sla_definitions     — thresholds per test/transaction ('*' = all transactions)
    └── ingest_events       — raw event log for non-LRE sources
```

Useful views:
```sql
SELECT * FROM v_latest_runs;          -- latest run per test
SELECT * FROM v_run_trend             -- last N runs, p95 per transaction
    WHERE run_recency <= 10;
SELECT * FROM v_sla_breaches;         -- all SLA failures
```

---

## Grafana dashboards

Both datasources are auto-provisioned. Example queries:

**InfluxDB — p95 trend (last 30 days)**
```flux
from(bucket: "perf-results")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "transaction_metrics")
  |> filter(fn: (r) => r.transaction_name == "Checkout")
  |> filter(fn: (r) => r._field == "p95_ms")
  |> aggregateWindow(every: 1d, fn: mean)
```

**Postgres — run comparison table**
```sql
SELECT run_number, started_at, transaction_name,
       p95_ms, p95_delta_pct, sla_passed
FROM v_run_trend
WHERE test_name = 'Checkout flow'
  AND run_recency <= 5
ORDER BY run_number DESC, transaction_name;
```

---

## Troubleshooting

**Event_meter is empty or missing from the zip**
The `ANALYZED RESULT` zip from the LRE API may only contain summary data
if the Analysis step in LRE did not complete. Check:
- LRE Analysis server is running and reachable from the LRE controller
- The run completed with state `Finished` (not `Stopped` or `Error`)
- Try increasing `LRE_RESULT_WAIT_SECONDS` — large runs take longer to analyse

**"No results database found in zip"**
Contents of the zip don't match expected file extensions. Run with
`--keep-zip` and inspect manually:
```bash
unzip -l $(Agent.TempDirectory)/lre_work/Results_*.zip
```

**Zero transactions after parsing**
The DB exists but `Event Type = 'Transaction'` matched nothing. Open in
SQLiteStudio and check:
```sql
SELECT DISTINCT "Event Type", COUNT(*) FROM Event_map GROUP BY "Event Type";
```
The type name may differ in your LRE version or locale.

**MDB file on Linux (no mdbtools)**
```bash
sudo apt-get install mdbtools
# Then re-run the pipeline or script
```

**LRE authentication 403 after first request**
CE 25.1+ is case-sensitive on the `/LoadTest` path in the cookie. Ensure
every request uses exactly `/LoadTest` — not `/Loadtest` or `/loadtest`.

---

## Production notes

- Change all passwords in `.env` before any non-local deployment
- For K8s deployment: each service maps to a Deployment + PVC;
  `.env` values become Kubernetes Secrets
- InfluxDB retention defaults to 1 year (`INFLUX_RETENTION` in `.env`)
- Never commit `.env` — only `.env.example` is safe to commit
