-- @file: sql/04_nlp_states.sql
-- @exports: NLP pipeline states, temporal classes, nlp_stage_outbox
-- @depends_on: sql/01_schema.sql
-- @purpose: Repeatable additive NLP state/constraint migration and durable stage handoffs.
BEGIN;
ALTER TABLE raw_reports DROP CONSTRAINT IF EXISTS raw_reports_temporal_status_check;
ALTER TABLE raw_reports ADD CONSTRAINT raw_reports_temporal_status_check CHECK
    (temporal_status IN ('active','cleared','planned','historical','unknown','scheduled_future','unrelated_noise'));
ALTER TABLE raw_reports DROP CONSTRAINT IF EXISTS raw_reports_pipeline_status_check;
ALTER TABLE raw_reports ADD CONSTRAINT raw_reports_pipeline_status_check CHECK
    (pipeline_status IN ('raw','filtered','extracted','geocoded','clustered','rejected','dead_letter',
        'FILTERED_ACTIVE','DROPPED_IRRELEVANT','DROPPED_NOT_ACTIVE','EXTRACTED'));
CREATE TABLE IF NOT EXISTS nlp_stage_outbox (
    report_id UUID NOT NULL REFERENCES raw_reports(report_id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN ('filter','extract')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    PRIMARY KEY (report_id, stage)
);
CREATE INDEX IF NOT EXISTS nlp_stage_outbox_pending_idx
    ON nlp_stage_outbox (created_at) WHERE delivered_at IS NULL;
COMMIT;
