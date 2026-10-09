-- EuLLM Agent Core: run state and audit.
-- The Core writes only to schema `core`; applications keep their own schema
-- and refer to a run by its id.

CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE core.runs (
    id            uuid PRIMARY KEY,
    tenant        text NOT NULL,
    profile       text NOT NULL,
    source        text NOT NULL,
    status        text NOT NULL CHECK (status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed')),
    input         text NOT NULL,
    output        text,
    error         text,
    iterations    integer NOT NULL DEFAULT 0,
    input_tokens  bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cost          double precision,
    tainted       boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE INDEX runs_tenant_created ON core.runs (tenant, created_at DESC);

CREATE TABLE core.llm_calls (
    id            bigserial PRIMARY KEY,
    tenant        text NOT NULL,
    run_id        uuid REFERENCES core.runs (id) ON DELETE CASCADE,
    provider      text NOT NULL,
    model         text NOT NULL,
    duration_ms   bigint NOT NULL,
    input_tokens  bigint,
    output_tokens bigint,
    cost          double precision,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_run ON core.llm_calls (run_id);
CREATE INDEX llm_calls_tenant_created ON core.llm_calls (tenant, created_at);

CREATE TABLE core.tool_calls (
    id              bigserial PRIMARY KEY,
    tenant          text NOT NULL,
    run_id          uuid NOT NULL REFERENCES core.runs (id) ON DELETE CASCADE,
    tool            text NOT NULL,
    argument_keys   text[] NOT NULL,
    decision        text NOT NULL,
    decision_reason text,
    approval_id     uuid,
    duration_ms     bigint NOT NULL,
    output_bytes    bigint,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tool_calls_run ON core.tool_calls (run_id);

CREATE TABLE core.approvals (
    id            uuid PRIMARY KEY,
    tenant        text NOT NULL,
    run_id        uuid NOT NULL REFERENCES core.runs (id) ON DELETE CASCADE,
    tool          text NOT NULL,
    arguments     jsonb NOT NULL,
    reason        text NOT NULL,
    status        text NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    decided_by    text,
    decision_note text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz
);
CREATE INDEX approvals_tenant_status ON core.approvals (tenant, status, created_at DESC);

-- Append-only record of everything above. Updates and deletes are refused.
CREATE TABLE core.audit_events (
    id         bigserial PRIMARY KEY,
    tenant     text NOT NULL,
    run_id     uuid,
    event      text NOT NULL,
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_tenant_created ON core.audit_events (tenant, created_at);

CREATE FUNCTION core.audit_events_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'core.audit_events is append-only';
END;
$$;

CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON core.audit_events
    FOR EACH ROW EXECUTE FUNCTION core.audit_events_immutable();
