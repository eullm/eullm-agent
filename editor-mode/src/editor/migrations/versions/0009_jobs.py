"""Background work leaves a trace, and a publication being sent is marked
before anything leaves.

- jobs: every analysis, discovery, draft and scheduled step, with its outcome,
  so a failure is visible instead of "started" forever, and the scheduler
  knows when maintenance and the weekly review last ran.
- publications.status 'sending': set before the request to the CMS, so a
  process that dies mid-way leaves an uncertain publication a person checks,
  never one that is silently sent twice.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE editor.jobs (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            kind text NOT NULL,
            subject text,
            status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
            error text,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        );
        CREATE INDEX jobs_recent ON editor.jobs (tenant_id, kind, started_at DESC);

        ALTER TABLE editor.publications DROP CONSTRAINT publications_status_check;
        ALTER TABLE editor.publications ADD CONSTRAINT publications_status_check
            CHECK (status IN ('pending_approval', 'approved', 'sending', 'rejected', 'published', 'failed'));
        DROP INDEX editor.publications_once;
        CREATE UNIQUE INDEX publications_once ON editor.publications (draft_id, target_id, mode)
            WHERE status IN ('pending_approval', 'approved', 'sending', 'published');
        """
    )
    op.execute(tenant_rls("jobs"))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        UPDATE editor.publications SET status = 'failed', error = 'downgrade: sending state removed'
            WHERE status = 'sending';
        DROP INDEX editor.publications_once;
        CREATE UNIQUE INDEX publications_once ON editor.publications (draft_id, target_id, mode)
            WHERE status IN ('pending_approval', 'approved', 'published');
        ALTER TABLE editor.publications DROP CONSTRAINT publications_status_check;
        ALTER TABLE editor.publications ADD CONSTRAINT publications_status_check
            CHECK (status IN ('pending_approval', 'approved', 'rejected', 'published', 'failed'));
        DROP TABLE editor.jobs;
        """
    )
