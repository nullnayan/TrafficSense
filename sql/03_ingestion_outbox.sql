-- @file: sql/03_ingestion_outbox.sql
-- @exports: ingestion_outbox
-- @depends_on: sql/01_schema.sql
-- @purpose: Repeatable additive migration for durable report-to-stream delivery.
BEGIN;
CREATE TABLE IF NOT EXISTS ingestion_outbox (
    report_id UUID PRIMARY KEY REFERENCES raw_reports(report_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ingestion_outbox_pending_idx
    ON ingestion_outbox (created_at) WHERE delivered_at IS NULL;
COMMIT;
