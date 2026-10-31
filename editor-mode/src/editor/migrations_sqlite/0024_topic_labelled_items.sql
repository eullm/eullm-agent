-- Alembic revision 0035: how many news items a topic had when the model last named it.
ALTER TABLE editor.topics ADD COLUMN labelled_items integer;
