-- Alembic revision 0040: Trend e piano finds each topic's latest opportunity score by topic.
CREATE INDEX editor.opportunity_scores_topic ON opportunity_scores (topic_id, site_id, computed_at);
