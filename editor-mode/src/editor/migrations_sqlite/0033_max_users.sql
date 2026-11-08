-- Alembic revision 0044: accesses included in a plan (people in the
-- newsroom, the director included); empty is no limit.
ALTER TABLE editor.plans ADD COLUMN max_users integer;
UPDATE editor.plans SET max_users = CASE code WHEN 'solo' THEN 1 WHEN 'starter' THEN 2
    WHEN 'pro' THEN 5 WHEN 'business' THEN 10 END;
ALTER TABLE editor.tenants ADD COLUMN max_users integer;
UPDATE editor.tenants SET max_users = (SELECT p.max_users FROM plans p WHERE p.code = tenants.plan);
