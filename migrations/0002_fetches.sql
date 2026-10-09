-- Requests made through POST /v1/fetch.
CREATE TABLE core.fetches (
    id          bigserial PRIMARY KEY,
    tenant      text NOT NULL,
    url         text NOT NULL,
    status      integer,
    bytes       bigint NOT NULL,
    duration_ms bigint NOT NULL,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX fetches_tenant_created ON core.fetches (tenant, created_at);
