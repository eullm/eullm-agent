"""Helpers shared by migrations."""

APP_ROLE = "editor_app"


def tenant_rls(table: str) -> str:
    """Enable and force row level security on editor.<table>, keyed on tenant_id."""
    return f"""
    ALTER TABLE editor.{table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE editor.{table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON editor.{table}
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
    GRANT SELECT, INSERT, UPDATE, DELETE ON editor.{table} TO {APP_ROLE};
    """
