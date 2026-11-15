-- ============================================================================
-- Migration 001: Core Platform Schema (PostgreSQL 17, RLS, Audit & Workflows)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
        CREATE ROLE app_runtime NOBYPASSRLS;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS TEXT AS $$
BEGIN
    RETURN NULLIF(current_setting('app.current_tenant_id', true), '');
END;
$$ LANGUAGE plpgsql STABLE;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    bundesland      TEXT NOT NULL DEFAULT 'NW',
    max_schutzklasse SMALLINT NOT NULL DEFAULT 3 CHECK (max_schutzklasse BETWEEN 0 AND 3),
    daily_token_budget INT NOT NULL DEFAULT 500000,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_act_register (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    system_id       TEXT NOT NULL,
    risikoklasse    TEXT NOT NULL CHECK (risikoklasse IN ('minimal', 'limited', 'high_risk')),
    approved_by_l3  TEXT NOT NULL,
    approved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, system_id)
);

ALTER TABLE ai_act_register ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_act_register FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_ai_act ON ai_act_register
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    workflow_id     TEXT NOT NULL,
    schutzklasse    SMALLINT NOT NULL CHECK (schutzklasse BETWEEN 0 AND 3),
    status          TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN', 'COMPLETED', 'FAILED_SAFE')),
    state_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_workflow_runs ON workflow_runs
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS workflow_steps (
    step_run_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    run_id          UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    step_id         TEXT NOT NULL,
    stufe           SMALLINT NOT NULL CHECK (stufe BETWEEN 0 AND 3),
    status          TEXT NOT NULL CHECK (status IN ('STARTED', 'COMPLETED', 'WAITING_HUMAN', 'FAILED')),
    begruendung     TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

ALTER TABLE workflow_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_workflow_steps ON workflow_steps
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS approvals (
    approval_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    run_id          UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    step_id         TEXT NOT NULL,
    level           TEXT NOT NULL CHECK (level IN ('L1', 'L2', 'L3')),
    payload_json    JSONB NOT NULL,
    payload_hash    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    decided_by      TEXT,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at      TIMESTAMPTZ
);

ALTER TABLE approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_approvals ON approvals
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS audit_subject_salts (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    subject_ref     TEXT NOT NULL,
    salt_bytes      BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, subject_ref)
);

ALTER TABLE audit_subject_salts ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_subject_salts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_subject_salts ON audit_subject_salts
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS audit_sequence_heads (
    tenant_id       TEXT PRIMARY KEY REFERENCES tenants(tenant_id),
    last_seq_no     BIGINT NOT NULL DEFAULT 0,
    last_hash       TEXT NOT NULL DEFAULT 'GENESIS'
);

ALTER TABLE audit_sequence_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_sequence_heads FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_seq_heads ON audit_sequence_heads
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE IF NOT EXISTS audit_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    seq_no          BIGINT NOT NULL,
    run_id          TEXT NOT NULL,
    step_id         TEXT NOT NULL,
    stufe           SMALLINT NOT NULL CHECK (stufe BETWEEN 0 AND 3),
    schutzklasse    SMALLINT NOT NULL CHECK (schutzklasse BETWEEN 0 AND 3),
    path_taken      TEXT NOT NULL CHECK (path_taken IN ('auto', 'human', 'no', 'yes')),
    subject_pseudonym TEXT,
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, seq_no)
);

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_audit_events ON audit_events
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;
