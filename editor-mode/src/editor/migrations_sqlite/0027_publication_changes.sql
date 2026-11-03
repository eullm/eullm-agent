-- Alembic revision 0038: a published article can be updated on the site
-- or withdrawn from it by the director.
ALTER TABLE editor.publications ADD COLUMN updated_at text;
ALTER TABLE editor.publications ADD COLUMN updated_by text;
ALTER TABLE editor.publications ADD COLUMN withdrawn_at text;
ALTER TABLE editor.publications ADD COLUMN withdrawn_by text;
