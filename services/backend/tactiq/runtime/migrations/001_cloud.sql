CREATE TABLE tactiq_cloud_schema (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version integer NOT NULL
);
INSERT INTO tactiq_cloud_schema(singleton, version) VALUES (true, 1);

CREATE TABLE tactiq_cloud_matches (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object' AND octet_length(state::text) <= 2097152),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, match_id)
);
CREATE TABLE tactiq_cloud_records (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    stream text NOT NULL CHECK (stream IN ('events', 'replay')),
    position bigint NOT NULL CHECK (position > 0),
    revision bigint NOT NULL CHECK (revision > 0),
    record_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 131072),
    PRIMARY KEY (tenant_id, match_id, stream, position),
    UNIQUE (tenant_id, match_id, stream, record_id),
    FOREIGN KEY (tenant_id, match_id) REFERENCES tactiq_cloud_matches ON DELETE CASCADE
);
CREATE INDEX tactiq_cloud_records_revision ON tactiq_cloud_records
    (tenant_id, match_id, stream, revision DESC, position DESC);
CREATE TABLE tactiq_cloud_jobs (
    tenant_id text NOT NULL,
    job_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 131072),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'cancelled', 'dead_letter')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= max_attempts),
    max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
    fence bigint NOT NULL DEFAULT 0,
    lease_owner text,
    lease_until timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    result jsonb,
    error text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, job_id)
);
CREATE TABLE tactiq_cloud_outbox (
    message_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    job_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 10),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dead_letter')),
    fence bigint NOT NULL DEFAULT 0,
    lease_owner text,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_id, job_id) REFERENCES tactiq_cloud_jobs ON DELETE CASCADE
);
CREATE INDEX tactiq_cloud_outbox_pending ON tactiq_cloud_outbox(available_at, message_id) WHERE status = 'pending';
CREATE TABLE tactiq_cloud_media (
    tenant_id text NOT NULL,
    match_id text NOT NULL,
    media_id text NOT NULL,
    size bigint NOT NULL CHECK (size > 0),
    content_type text NOT NULL,
    etag text NOT NULL,
    PRIMARY KEY (tenant_id, match_id, media_id),
    FOREIGN KEY (tenant_id, match_id) REFERENCES tactiq_cloud_matches ON DELETE CASCADE
);
