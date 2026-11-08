-- Alembic revision 0043: credit prices raised, an article beyond the plan
-- 25 -> 100 credits, a cover redrawn 3 -> 5; prices changed by hand stay.
UPDATE editor.credit_prices SET credits = 100 WHERE action = 'article' AND credits = 25;
UPDATE editor.credit_prices SET credits = 5 WHERE action = 'image' AND credits = 3;
