"""Access to the dashboard and API: tokens per tenant with a role.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE editor.access_tokens (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            name text NOT NULL,
            token_sha256 text NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{{64}}$'),
            role text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
            created_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz
        );
        {tenant_rls("access_tokens")}
        -- Who is calling is known before the tenant is: only this function
        -- reads tokens across tenants, and it returns one row at most.
        CREATE FUNCTION editor.resolve_token(digest text)
            RETURNS TABLE (tenant_id text, name text, role text)
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$ SELECT tenant_id, name, role FROM editor.access_tokens
                  WHERE token_sha256 = digest AND revoked_at IS NULL $$;
        ALTER TABLE editor.access_tokens NO FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON FUNCTION editor.resolve_token(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION editor.resolve_token(text) TO {APP_ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS editor.resolve_token(text)")
    op.execute("DROP TABLE IF EXISTS editor.access_tokens")
