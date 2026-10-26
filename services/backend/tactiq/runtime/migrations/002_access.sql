CREATE TABLE tactiq_access_schema (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version integer NOT NULL
);
INSERT INTO tactiq_access_schema VALUES (true, 2);

CREATE TABLE tactiq_access_budget_guard (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton)
);
INSERT INTO tactiq_access_budget_guard VALUES (true);

CREATE TABLE tactiq_access_tenants (
    id text PRIMARY KEY,
    guest boolean NOT NULL,
    expires double precision,
    promoted_to text REFERENCES tactiq_access_tenants(id),
    expired boolean NOT NULL DEFAULT false,
    CHECK ((guest AND expires IS NOT NULL AND expires > 0 AND expires < 'Infinity'::double precision)
        OR (NOT guest AND expires IS NULL)),
    CHECK (promoted_to IS NULL OR (guest AND NOT expired AND promoted_to <> id))
);
CREATE INDEX tactiq_access_tenant_expiry ON tactiq_access_tenants(expires, id)
    WHERE guest AND NOT expired AND promoted_to IS NULL;
CREATE INDEX tactiq_access_promotions ON tactiq_access_tenants(promoted_to)
    WHERE promoted_to IS NOT NULL;

CREATE TABLE tactiq_access_accounts (
    issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 512),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 256),
    account_id text NOT NULL UNIQUE,
    tenant_id text NOT NULL UNIQUE REFERENCES tactiq_access_tenants(id),
    PRIMARY KEY (issuer, subject)
);
CREATE TABLE tactiq_access_sessions (
    digest text PRIMARY KEY CHECK (digest ~ '^[0-9a-f]{64}$'),
    tenant_id text NOT NULL REFERENCES tactiq_access_tenants(id),
    subject text NOT NULL,
    expires double precision NOT NULL CHECK (expires > 0 AND expires < 'Infinity'::double precision),
    revoked boolean NOT NULL DEFAULT false
);
CREATE INDEX tactiq_access_sessions_tenant ON tactiq_access_sessions(tenant_id);

CREATE TABLE tactiq_access_trial_windows (
    ip_hash text PRIMARY KEY CHECK (ip_hash ~ '^[0-9a-f]{64}$'),
    starts double precision NOT NULL CHECK (starts >= 0 AND starts < 'Infinity'::double precision),
    used integer NOT NULL CHECK (used BETWEEN 0 AND 3)
);
CREATE TABLE tactiq_access_analyses (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tactiq_access_tenants(id),
    operation_id text NOT NULL CHECK (length(operation_id) BETWEEN 1 AND 128),
    kind text NOT NULL CHECK (kind IN ('scenario', 'upload', 'link')),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    UNIQUE (tenant_id, operation_id)
);
CREATE TABLE tactiq_access_budget_policies (
    period_start double precision NOT NULL,
    period_end double precision NOT NULL,
    global_limit bigint NOT NULL CHECK (global_limit BETWEEN 0 AND 150000000),
    tenant_limit bigint NOT NULL CHECK (tenant_limit BETWEEN 0 AND global_limit),
    PRIMARY KEY (period_start, period_end),
    CHECK (period_start >= 0 AND period_start < period_end AND period_end < 'Infinity'::double precision)
);
CREATE TABLE tactiq_access_usage (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tactiq_access_tenants(id),
    operation_id text NOT NULL CHECK (length(operation_id) BETWEEN 1 AND 128),
    meter text NOT NULL CHECK (meter IN ('model_tokens', 'speech_characters', 'processing', 'storage')),
    units bigint NOT NULL CHECK (units > 0),
    maximum_cost bigint NOT NULL CHECK (maximum_cost > 0),
    period_start double precision NOT NULL,
    period_end double precision NOT NULL,
    status text NOT NULL CHECK (status IN ('reserved', 'settled', 'released')),
    actual_units bigint CHECK (actual_units BETWEEN 0 AND units),
    actual_cost bigint CHECK (actual_cost BETWEEN 0 AND maximum_cost),
    UNIQUE (tenant_id, operation_id),
    FOREIGN KEY (period_start, period_end) REFERENCES tactiq_access_budget_policies,
    CHECK ((status = 'settled' AND actual_units IS NOT NULL AND actual_cost IS NOT NULL)
        OR (status <> 'settled' AND actual_units IS NULL AND actual_cost IS NULL))
);
CREATE INDEX tactiq_access_usage_period ON tactiq_access_usage(period_start, period_end);
CREATE INDEX tactiq_access_usage_tenant ON tactiq_access_usage(tenant_id, period_start, period_end);

CREATE TABLE tactiq_access_cleanup (
    tenant_id text PRIMARY KEY REFERENCES tactiq_access_tenants(id),
    analysis_ids jsonb NOT NULL CHECK (jsonb_typeof(analysis_ids) = 'array')
);
CREATE TABLE tactiq_access_logins (
    binding_hash text PRIMARY KEY CHECK (binding_hash ~ '^[0-9a-f]{64}$'),
    state_hash text NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    flow jsonb NOT NULL CHECK (jsonb_typeof(flow) = 'object' AND octet_length(flow::text) <= 16384),
    guest_digest text REFERENCES tactiq_access_sessions(digest),
    expires double precision NOT NULL CHECK (expires > 0 AND expires < 'Infinity'::double precision)
);
CREATE INDEX tactiq_access_logins_expiry ON tactiq_access_logins(expires);
CREATE INDEX tactiq_access_logins_guest ON tactiq_access_logins(guest_digest)
    WHERE guest_digest IS NOT NULL;
