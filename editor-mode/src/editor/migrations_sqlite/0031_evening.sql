-- Alembic revision 0042: plans and newsrooms say whether they include the
-- evening analysis (Il Punto della Sera); Pro, Business and Agency do.
ALTER TABLE editor.plans ADD COLUMN evening boolean NOT NULL DEFAULT 0;
UPDATE editor.plans SET evening = 1 WHERE code IN ('pro', 'business', 'agency');
ALTER TABLE editor.tenants ADD COLUMN evening boolean NOT NULL DEFAULT 1;
UPDATE editor.tenants SET evening = COALESCE((SELECT p.evening FROM plans p WHERE p.code = tenants.plan), 1);
