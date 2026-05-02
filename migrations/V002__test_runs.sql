-- TestRuns: single execution of a Test (like a LoadRunner run)
CREATE TYPE run_status AS ENUM ('pending', 'running', 'passed', 'failed', 'aborted');

CREATE TABLE test_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_id      UUID NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    status       run_status NOT NULL DEFAULT 'pending',
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    run_metadata JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_test_runs_test_id ON test_runs(test_id);
CREATE INDEX idx_test_runs_status  ON test_runs(status);
