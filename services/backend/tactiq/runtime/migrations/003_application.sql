CREATE TABLE IF NOT EXISTS tactiq_cloud_application_schema (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton)
);
INSERT INTO tactiq_cloud_application_schema VALUES (true) ON CONFLICT DO NOTHING;
ALTER TABLE tactiq_cloud_jobs DROP CONSTRAINT IF EXISTS tactiq_cloud_jobs_status_check;
ALTER TABLE tactiq_cloud_jobs ADD CONSTRAINT tactiq_cloud_jobs_status_check
    CHECK (status IN ('queued','running','succeeded','cancelled','dead_letter','paused'));
CREATE TABLE IF NOT EXISTS tactiq_cloud_inputs (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    locator text NOT NULL CHECK (octet_length(locator)<=8192),
    PRIMARY KEY (tenant_id,match_id),
    FOREIGN KEY (tenant_id,match_id) REFERENCES tactiq_cloud_matches ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS tactiq_cloud_jobs_recovery ON tactiq_cloud_jobs(status,updated_at);
CREATE INDEX IF NOT EXISTS tactiq_cloud_replay_clock ON tactiq_cloud_records
    (tenant_id,match_id,((payload->>'timestamp_ms')::bigint) DESC) WHERE stream='replay';
CREATE TABLE IF NOT EXISTS tactiq_cloud_processing_window (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    starts timestamptz NOT NULL,
    admitted integer NOT NULL CHECK (admitted>=0),
    approved_limit integer NOT NULL CHECK (approved_limit BETWEEN 1 AND 10000)
);
CREATE TABLE IF NOT EXISTS tactiq_cloud_scripts (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    script_id text NOT NULL,
    payload jsonb NOT NULL CHECK (octet_length(payload::text)<=65536),
    PRIMARY KEY (tenant_id,match_id,script_id),
    FOREIGN KEY (tenant_id,match_id) REFERENCES tactiq_cloud_matches ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tactiq_cloud_generation_results (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    operation_id text NOT NULL,
    payload jsonb NOT NULL CHECK (octet_length(payload::text)<=65536),
    PRIMARY KEY (tenant_id,match_id,operation_id),
    FOREIGN KEY (tenant_id,match_id) REFERENCES tactiq_cloud_matches ON DELETE CASCADE
);
