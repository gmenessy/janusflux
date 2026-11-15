-- ============================================================================
-- Migration 002: Skill-Registry, Mandanten-Skill-Freigabe & Ausführungs-Journal
-- ============================================================================

CREATE TABLE skill_catalog (
    skill_id        TEXT NOT NULL,
    version         TEXT NOT NULL,
    name            TEXT NOT NULL,
    art             TEXT NOT NULL CHECK (art IN ('atomar', 'zusammengesetzt')),
    wirkung         TEXT NOT NULL CHECK (wirkung IN ('read', 'reversible_write', 'irreversible')),
    stufen          SMALLINT[] NOT NULL,
    max_schutzklasse SMALLINT NOT NULL CHECK (max_schutzklasse BETWEEN 0 AND 3),
    manifest_json   JSONB NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (skill_id, version)
);

CREATE TABLE tenant_skill_bindings (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    skill_id        TEXT NOT NULL,
    skill_version   TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    approved_by_l3  TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, skill_id),
    FOREIGN KEY (skill_id, skill_version) REFERENCES skill_catalog(skill_id, version)
);

ALTER TABLE tenant_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_skill_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_skill_bindings ON tenant_skill_bindings
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE skill_executions (
    execution_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL REFERENCES tenants(tenant_id),
    run_id              UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    skill_id            TEXT NOT NULL,
    skill_version       TEXT NOT NULL,
    detection_source    TEXT NOT NULL CHECK (detection_source IN ('stage0_explicit', 'stage0_trigger', 'stage1_decision_dmn')),
    detection_conf      NUMERIC(5,4) NOT NULL,
    routing_action      TEXT NOT NULL CHECK (routing_action IN ('AUTO_EXECUTE', 'REQUIRE_CLARIFICATION', 'ROUTE_TO_WORKBENCH', 'REJECT_NO_SKILL')),
    status              TEXT NOT NULL CHECK (status IN ('COMPLETED', 'WAITING_HUMAN', 'CLARIFICATION_NEEDED', 'FAILED_SAFE', 'SKIPPED')),
    llm_calls_used      SMALLINT NOT NULL DEFAULT 0,
    latency_ms          INT NOT NULL,
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE skill_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_skill_execs ON skill_executions
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE ON skill_catalog, tenant_skill_bindings, skill_executions TO app_runtime;
