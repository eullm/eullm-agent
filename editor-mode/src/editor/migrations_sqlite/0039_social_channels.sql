-- Alembic revision 0051: social channels (sites.kind, sites.handle), videos included in the plan.
ALTER TABLE editor.sites ADD COLUMN kind text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'social'));
ALTER TABLE editor.sites ADD COLUMN handle text;
ALTER TABLE editor.plans ADD COLUMN max_videos_month integer CHECK (max_videos_month >= 0);
ALTER TABLE editor.tenants ADD COLUMN max_videos_month integer CHECK (max_videos_month >= 0);
UPDATE editor.plans SET max_videos_month = 0;
UPDATE editor.tenants SET max_videos_month = 0 WHERE plan IS NOT NULL AND plan <> '';
ALTER TABLE editor.videos ADD COLUMN included boolean NOT NULL DEFAULT 0;
ALTER TABLE editor.videos ADD COLUMN hype real;
ALTER TABLE editor.videos ADD COLUMN gain real;
ALTER TABLE editor.videos ADD COLUMN auto boolean NOT NULL DEFAULT 0;
