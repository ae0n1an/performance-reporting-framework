-- FireAndForgetMessages: async messages dispatched during a run
CREATE TYPE message_status AS ENUM ('sent', 'delivered', 'failed', 'timeout');

CREATE TABLE fire_and_forget_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    correlation_id  TEXT NOT NULL,
    topic           TEXT,
    payload         JSONB NOT NULL DEFAULT '{}',
    status          message_status NOT NULL DEFAULT 'sent',
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    error_message   TEXT,
    source          TEXT
);

CREATE INDEX idx_messages_run_id         ON fire_and_forget_messages(run_id);
CREATE INDEX idx_messages_correlation_id ON fire_and_forget_messages(correlation_id);

-- CorrelationLinks: ties transactions and messages together by correlation_id
-- Allows a single ID to span async boundaries and reconstruct end-to-end traces
CREATE TABLE correlation_links (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id TEXT NOT NULL,
    transaction_id UUID REFERENCES transactions(id) ON DELETE CASCADE,
    message_id     UUID REFERENCES fire_and_forget_messages(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_correlation_link_has_target
        CHECK (transaction_id IS NOT NULL OR message_id IS NOT NULL)
);

CREATE INDEX idx_correlation_links_id ON correlation_links(correlation_id);
