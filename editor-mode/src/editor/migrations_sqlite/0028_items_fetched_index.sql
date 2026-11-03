-- Alembic revision 0039: the Desk's counts of the day's news read the day, not the whole archive.
CREATE INDEX editor.source_items_fetched ON source_items (tenant_id, fetched_at);
