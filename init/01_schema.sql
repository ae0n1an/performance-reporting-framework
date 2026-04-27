-- =============================================================
--  Performance Results DB — PostgreSQL Schema
--  Runs on first container start via docker-entrypoint-initdb.d
-- =============================================================

-- ── Extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- for fuzzy name search


-- =============================================================
--  1. TEST DEFINITIONS
--     Describes what a test is, independent of any single run.
--     A test can be a LoadRunner scenario, a k6 script, an
--     Azure Pipeline job, etc.
-- =============================================================
CREATE TABLE test_definitions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT        NOT NULL,
    description     TEXT,
    source_tool     TEXT        NOT NULL,  -- 'loadrunner', 'k6', 'jmeter', 'azure_pipeline', 'event_hub', etc.
    environment     TEXT        NOT NULL,  -- 'dev', 'staging', 'prod', etc.
    application     TEXT        NOT NULL,  -- which app / service under test
    lre_project     TEXT,                  -- LRE project name if applicable
    lre_scenario_id TEXT,                  -- LRE scenario ID if applicable
    tags            TEXT[]      DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_test_def_source  ON test_definitions(source_tool);
CREATE INDEX idx_test_def_app     ON test_definitions(application);
CREATE INDEX idx_test_def_env     ON test_definitions(environment);


-- =============================================================
--  2. TEST RUNS
--     One row per execution. This is the main run registry —
--     the row is created when the run starts and updated when
--     it completes (or fails).
-- =============================================================
CREATE TYPE run_status AS ENUM (
    'triggered',    -- pipeline/API call made
    'running',      -- test actively executing
    'completed',    -- finished normally
    'failed',       -- test framework error or hard failure
    'aborted',      -- manually cancelled
    'data_missing', -- run finished but LRE/tool data incomplete/suspect
    'data_partial'  -- run finished, some data exported but with warnings
);

CREATE TYPE data_quality AS ENUM (
    'good',         -- full export, validated
    'partial',      -- some transactions missing or export incomplete
    'fallback',     -- used HTML scrape or secondary source, not MDB
    'unknown'       -- not yet assessed
);

CREATE TABLE test_runs (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_definition_id  UUID        NOT NULL REFERENCES test_definitions(id),

    -- Identity
    run_number          SERIAL,     -- auto-incrementing friendly run number
    build_id            TEXT,       -- Azure DevOps build ID
    pipeline_name       TEXT,       -- Azure Pipeline name
    git_branch          TEXT,
    git_commit          TEXT,
    triggered_by        TEXT,       -- 'schedule', 'manual', 'ci', 'event_hub'

    -- Timing
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    duration_seconds    INT GENERATED ALWAYS AS (
                            EXTRACT(EPOCH FROM (completed_at - started_at))::INT
                        ) STORED,

    -- Status & data quality
    status              run_status  NOT NULL DEFAULT 'triggered',
    data_quality        data_quality NOT NULL DEFAULT 'unknown',
    data_quality_notes  TEXT,       -- human-readable notes on any issues (e.g. "MDB incomplete, used HTML fallback")

    -- LRE-specific
    lre_run_id          TEXT,       -- LRE internal run ID
    lre_mdb_generated   BOOLEAN,    -- whether MDB was confirmed present
    lre_export_path     TEXT,       -- where raw export files were stored

    -- Load profile summary (denormalised for quick queries)
    vuser_count         INT,
    ramp_up_seconds     INT,
    steady_state_seconds INT,
    ramp_down_seconds   INT,

    -- Top-level SLA result
    sla_passed          BOOLEAN,    -- overall pass/fail across all SLAs
    sla_summary         JSONB,      -- { "passed": 3, "failed": 1, "warnings": 0 }

    -- Confluence
    confluence_page_id  TEXT,
    confluence_page_url TEXT,

    -- Metadata
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_runs_test_def     ON test_runs(test_definition_id);
CREATE INDEX idx_runs_status       ON test_runs(status);
CREATE INDEX idx_runs_started      ON test_runs(started_at DESC);
CREATE INDEX idx_runs_build        ON test_runs(build_id);
CREATE INDEX idx_runs_sla          ON test_runs(sla_passed);


-- =============================================================
--  3. TRANSACTION RESULTS
--     One row per transaction per run. These mirror what you
--     would export from LRE or any other tool. They also act
--     as the bridge for run-over-run comparison queries.
-- =============================================================
CREATE TABLE transaction_results (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id              UUID        NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    transaction_name    TEXT        NOT NULL,

    -- Core response time metrics (all in milliseconds)
    avg_ms              NUMERIC(10,2),
    min_ms              NUMERIC(10,2),
    max_ms              NUMERIC(10,2),
    p50_ms              NUMERIC(10,2),
    p75_ms              NUMERIC(10,2),
    p90_ms              NUMERIC(10,2),
    p95_ms              NUMERIC(10,2),
    p99_ms              NUMERIC(10,2),
    stddev_ms           NUMERIC(10,2),

    -- Throughput & errors
    total_hits          BIGINT,
    hits_per_second     NUMERIC(10,3),
    error_count         BIGINT,
    error_rate_pct      NUMERIC(6,3),   -- 0.000 – 100.000

    -- SLA outcome for this specific transaction
    sla_passed          BOOLEAN,
    sla_breach_metrics  TEXT[],         -- which metrics breached, e.g. ['p95_ms', 'error_rate_pct']

    -- Comparison to previous run (populated post-run by pipeline script)
    prev_run_id         UUID REFERENCES test_runs(id),
    p95_delta_pct       NUMERIC(8,2),   -- % change vs prev run p95
    avg_delta_pct       NUMERIC(8,2),

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_txn_run_id   ON transaction_results(run_id);
CREATE INDEX idx_txn_name     ON transaction_results(transaction_name);
CREATE INDEX idx_txn_sla      ON transaction_results(sla_passed);
-- Composite: fast lookup for "all results for transaction X across recent runs"
CREATE INDEX idx_txn_name_run ON transaction_results(transaction_name, run_id);


-- =============================================================
--  4. SLA DEFINITIONS
--     What "pass" means for each test + transaction combination.
--     Decoupled from results so you can change thresholds
--     without touching historical data.
-- =============================================================
CREATE TABLE sla_definitions (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_definition_id  UUID        NOT NULL REFERENCES test_definitions(id),
    transaction_name    TEXT        NOT NULL,   -- '*' means applies to all transactions

    -- Thresholds (NULL = not enforced)
    max_avg_ms          NUMERIC(10,2),
    max_p90_ms          NUMERIC(10,2),
    max_p95_ms          NUMERIC(10,2),
    max_p99_ms          NUMERIC(10,2),
    max_error_rate_pct  NUMERIC(6,3),
    min_hits_per_second NUMERIC(10,3),

    severity            TEXT DEFAULT 'hard',    -- 'hard' (fails pipeline) or 'warning' (flags only)
    active              BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sla_test_def ON sla_definitions(test_definition_id);
CREATE UNIQUE INDEX idx_sla_unique ON sla_definitions(test_definition_id, transaction_name);


-- =============================================================
--  5. EVENT SOURCE LOG
--     Generic ingest log for non-LRE sources:
--     Azure Event Hub events, pipeline webhook payloads, etc.
--     Raw payload stored as JSONB for flexibility — parsed
--     fields are normalised into test_runs + transaction_results
--     by the ingestion script.
-- =============================================================
CREATE TABLE ingest_events (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          TEXT        NOT NULL,   -- 'event_hub', 'azure_pipeline_webhook', 'manual_api', etc.
    event_type      TEXT        NOT NULL,   -- 'run_started', 'run_completed', 'metric_batch', etc.
    raw_payload     JSONB       NOT NULL,
    run_id          UUID        REFERENCES test_runs(id),   -- linked after processing
    processed       BOOLEAN     DEFAULT FALSE,
    processed_at    TIMESTAMPTZ,
    error_message   TEXT,       -- populated if processing failed
    received_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ingest_source    ON ingest_events(source);
CREATE INDEX idx_ingest_processed ON ingest_events(processed);
CREATE INDEX idx_ingest_run       ON ingest_events(run_id);
CREATE INDEX idx_ingest_received  ON ingest_events(received_at DESC);


-- =============================================================
--  6. USEFUL VIEWS
-- =============================================================

-- Latest run per test definition with SLA summary
CREATE VIEW v_latest_runs AS
SELECT DISTINCT ON (r.test_definition_id)
    r.*,
    td.name         AS test_name,
    td.source_tool,
    td.application,
    td.environment
FROM test_runs r
JOIN test_definitions td ON td.id = r.test_definition_id
WHERE r.status IN ('completed', 'data_missing', 'data_partial')
ORDER BY r.test_definition_id, r.started_at DESC;


-- Run-over-run comparison for the last 10 runs of each test
CREATE VIEW v_run_trend AS
SELECT
    r.id                            AS run_id,
    r.run_number,
    td.name                         AS test_name,
    td.application,
    td.environment,
    r.started_at,
    r.status,
    r.data_quality,
    r.vuser_count,
    r.sla_passed,
    r.duration_seconds,
    t.transaction_name,
    t.p95_ms,
    t.avg_ms,
    t.error_rate_pct,
    t.p95_delta_pct,
    t.sla_passed                    AS txn_sla_passed,
    ROW_NUMBER() OVER (
        PARTITION BY r.test_definition_id, t.transaction_name
        ORDER BY r.started_at DESC
    )                               AS run_recency   -- 1 = most recent
FROM test_runs r
JOIN test_definitions td ON td.id = r.test_definition_id
JOIN transaction_results t ON t.run_id = r.id
WHERE r.status IN ('completed', 'data_partial');


-- SLA breach summary across all recent runs
CREATE VIEW v_sla_breaches AS
SELECT
    td.name         AS test_name,
    td.application,
    td.environment,
    r.id            AS run_id,
    r.run_number,
    r.started_at,
    r.data_quality,
    t.transaction_name,
    t.p95_ms,
    t.error_rate_pct,
    t.sla_breach_metrics
FROM test_runs r
JOIN test_definitions td ON td.id = r.test_definition_id
JOIN transaction_results t ON t.run_id = r.id
WHERE t.sla_passed = FALSE
ORDER BY r.started_at DESC;


-- =============================================================
--  7. UPDATED_AT AUTO-TRIGGER
-- =============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_test_definitions_updated
    BEFORE UPDATE ON test_definitions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_test_runs_updated
    BEFORE UPDATE ON test_runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================
--  8. SEED — example test definitions to get started
-- =============================================================
INSERT INTO test_definitions (name, source_tool, environment, application, lre_project, tags)
VALUES
    ('Checkout flow — load test',  'loadrunner',      'staging', 'ecommerce-api', 'MyLREProject', ARRAY['smoke','regression']),
    ('Search API — spike test',    'loadrunner',      'staging', 'search-service', 'MyLREProject', ARRAY['spike']),
    ('Auth service — pipeline job','azure_pipeline',  'staging', 'auth-service',  NULL,           ARRAY['ci']),
    ('Event Hub ingest test',       'event_hub',      'staging', 'data-pipeline', NULL,           ARRAY['integration']);

INSERT INTO sla_definitions (test_definition_id, transaction_name, max_p95_ms, max_error_rate_pct, severity)
SELECT id, '*', 2000, 1.0, 'hard'
FROM test_definitions
WHERE source_tool = 'loadrunner';
