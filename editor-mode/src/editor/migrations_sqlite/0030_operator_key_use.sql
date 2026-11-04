-- Alembic revision 0041: the console says when each API key was last used.
ALTER TABLE editor.operator_tokens ADD COLUMN last_used_at TEXT;
