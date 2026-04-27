# Performance Results Stack

InfluxDB 2 + PostgreSQL + Grafana, wired for performance test result ingestion
from LoadRunner Enterprise, Azure Pipelines, Azure Event Hub, and any future tool.

## Stack

| Service    | Port | Purpose                                      |
|------------|------|----------------------------------------------|
| InfluxDB 2 | 8086 | Time-series metrics — Grafana dashboards     |
| PostgreSQL | 5432 | Run registry, SLA definitions, comparisons  |
| Grafana    | 3000 | Dashboards (reads both DBs)                 |

## Quick start

```bash
cp .env.example .env
# Edit .env — change all passwords before any non-local deployment

docker compose up -d
# First start: Postgres runs init/01_schema.sql automatically
# InfluxDB initialises with the bucket and token from .env
# Grafana provisions both datasources automatically
```

Grafana: http://localhost:3000  
InfluxDB UI: http://localhost:8086

## Ingest a LoadRunner CSV

```bash
pip install influxdb-client psycopg2-binary python-dotenv

# First, get your test_definition_id from Postgres:
# psql -h localhost -U perfuser -d perfdb
# SELECT id, name FROM test_definitions;

python perf_ingest.py lre \
  --csv-path       /path/to/lre_export.csv \
  --test-def-id    <uuid-from-postgres> \
  --lre-run-id     12345 \
  --vusers         100 \
  --build-id       $(Build.BuildId) \
  --fail-on-sla                        # omit if you don't want a hard gate
```

### LRE data quality flags

The ingest script is defensive by design:

- If the CSV is empty or has 0 transactions → run stored as `data_missing`
- If the CSV has very few transactions → stored as `data_partial`  
- If export succeeds normally → stored as `good`
- Pass `--no-mdb` if you know the MDB was not generated

A Confluence report is always created regardless of quality — a warning banner
is shown on partial/missing runs so the team knows to verify manually.

## Ingest from Event Hub / generic source

Adapt `example_event_hub_payload.json` to match your source's schema,
then update the mapping in `cmd_generic()` in `perf_ingest.py`.

```bash
python perf_ingest.py generic \
  --payload      event.json \
  --source       event_hub \
  --test-def-id  <uuid>
```

## Azure Pipeline integration

Copy `azure-pipeline-perf.yml` and add it as a stage in your existing pipeline.
Set these pipeline variables / secrets in Azure DevOps:

```
LRE_HOST, LRE_USER, LRE_PASSWORD, LRE_PROJECT, LRE_DOMAIN
INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET
POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
```

## Schema overview

```
test_definitions    — what a test is (name, tool, app, environment)
    │
test_runs           — one row per execution (status, data_quality, SLA result)
    │
transaction_results — one row per transaction per run (all percentiles, deltas)
    │
sla_definitions     — thresholds per test + transaction (or '*' for all)
ingest_events       — raw event log for non-LRE sources
```

### Useful views

```sql
-- Latest run per test
SELECT * FROM v_latest_runs;

-- Last 10 runs, p95 trend per transaction
SELECT * FROM v_run_trend WHERE run_recency <= 10;

-- All SLA breaches
SELECT * FROM v_sla_breaches;
```

## Grafana setup

Both datasources are provisioned automatically on first start:

- **InfluxDB - Perf Results** (default) — use Flux queries for time-series charts
- **PostgreSQL - Run Registry** — use SQL for run comparison tables and SLA views

### Example Flux query (p95 trend for a transaction)

```flux
from(bucket: "perf-results")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "transaction_metrics")
  |> filter(fn: (r) => r.transaction_name == "Checkout")
  |> filter(fn: (r) => r._field == "p95_ms")
  |> aggregateWindow(every: 1d, fn: mean)
```

### Example SQL query (run comparison table)

```sql
SELECT run_number, started_at, transaction_name,
       p95_ms, p95_delta_pct, sla_passed
FROM v_run_trend
WHERE test_name = 'Checkout flow — load test'
  AND run_recency <= 5
ORDER BY run_number DESC, transaction_name;
```

## Production notes

- Change all passwords in `.env` before deploying anywhere non-local
- For K8s deployment: each service maps to a Deployment + PVC; the `.env`
  values become Kubernetes Secrets
- InfluxDB retention is set to 1 year by default — adjust `INFLUX_RETENTION` in `.env`
- Postgres data persists in a named volume; back it up like any other DB
