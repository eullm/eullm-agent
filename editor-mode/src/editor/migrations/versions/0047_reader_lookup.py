"""Feedback from readers, on a server: the public form finds its site, and
the confirmation link its message, across newsrooms. Row level security is
forced on the owner too (a server's owner is not a superuser), so the
lookups go through two functions that look newsroom by newsroom
(2026-11-11: on the server every site answered "enabled": false).

Revision ID: 0047
Revises: 0046
"""

from alembic import op

from editor.migrations.rls import APP_ROLE

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION editor.feedback_sites()
            RETURNS TABLE (tenant_id text, id bigint, domain text, name text, language text)
            LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$
            DECLARE t text; prev text := current_setting('app.tenant_id', true);
            BEGIN
                FOR t IN SELECT x FROM editor.active_tenants() AS x LOOP
                    PERFORM set_config('app.tenant_id', t, true);
                    RETURN QUERY SELECT s.tenant_id, s.id, s.domain, s.name, s.language
                                 FROM editor.sites s WHERE s.feedback ORDER BY s.id;
                END LOOP;
                PERFORM set_config('app.tenant_id', coalesce(prev, ''), true);
            END $$;
        CREATE FUNCTION editor.reader_message_by_token(digest text)
            RETURNS TABLE (id bigint, tenant_id text, status text, message text, created_at timestamptz,
                           name text, domain text)
            LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$
            DECLARE t text; prev text := current_setting('app.tenant_id', true);
            BEGIN
                FOR t IN SELECT x FROM editor.active_tenants() AS x LOOP
                    PERFORM set_config('app.tenant_id', t, true);
                    RETURN QUERY SELECT r.id, r.tenant_id, r.status, r.message, r.created_at, s.name, s.domain
                                 FROM editor.reader_messages r JOIN editor.sites s ON s.id = r.site_id
                                 WHERE r.token_sha256 = digest;
                END LOOP;
                PERFORM set_config('app.tenant_id', coalesce(prev, ''), true);
            END $$;
        REVOKE ALL ON FUNCTION editor.feedback_sites() FROM PUBLIC;
        REVOKE ALL ON FUNCTION editor.reader_message_by_token(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION editor.feedback_sites() TO {APP_ROLE};
        GRANT EXECUTE ON FUNCTION editor.reader_message_by_token(text) TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION editor.reader_message_by_token(text); DROP FUNCTION editor.feedback_sites();")
