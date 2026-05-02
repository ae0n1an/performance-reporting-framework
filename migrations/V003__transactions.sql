CREATE TYPE transaction_kind   AS ENUM ('transaction', 'message');
CREATE TYPE transaction_status AS ENUM ('pass', 'fail', 'stop');

CREATE TABLE transactions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id               UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    kind                 transaction_kind   NOT NULL DEFAULT 'transaction',
    name                 TEXT NOT NULL,
    status               transaction_status NOT NULL DEFAULT 'pass',
    start_time           TIMESTAMPTZ NOT NULL,
    end_time             TIMESTAMPTZ,
    duration_ms          INTEGER,
    -- Correlation IDs link this transaction's start/end boundary to other transactions.
    -- start_correlation_id: this tx begins because it received/consumed this ID.
    -- end_correlation_id:   this tx fires/emits this ID when it finishes.
    start_correlation_id TEXT,
    end_correlation_id   TEXT,
    -- Message-specific fields (null for kind='transaction')
    topic                TEXT,
    payload              JSONB NOT NULL DEFAULT '{}',
    source               TEXT,
    acknowledged_at      TIMESTAMPTZ,
    -- Common fields
    vuser_id             TEXT,
    iteration            INTEGER NOT NULL DEFAULT 1,
    error_message        TEXT,
    extra                JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_transactions_run_id     ON transactions(run_id);
CREATE INDEX idx_transactions_name       ON transactions(name);
CREATE INDEX idx_transactions_start_corr ON transactions(start_correlation_id) WHERE start_correlation_id IS NOT NULL;
CREATE INDEX idx_transactions_end_corr   ON transactions(end_correlation_id)   WHERE end_correlation_id   IS NOT NULL;

-- Sub-steps / checkpoints within a transaction
CREATE TABLE transaction_steps (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    sequence       INTEGER NOT NULL DEFAULT 0,
    start_time     TIMESTAMPTZ,
    end_time       TIMESTAMPTZ,
    duration_ms    INTEGER,
    status         transaction_status NOT NULL DEFAULT 'pass',
    extra          JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_transaction_steps_tx_id ON transaction_steps(transaction_id);
