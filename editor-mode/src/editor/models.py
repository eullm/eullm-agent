"""Table definitions of the editor schema (the migrations are the source of truth)."""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    REAL,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="editor")


def _tenant() -> Column:
    return Column("tenant_id", Text, nullable=False)


tenants = Table(
    "tenants",
    metadata,
    Column("tenant_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("plan", Text),
    Column("max_sites", Integer),
    Column("max_active_sources", Integer),
    Column("max_items_per_day", Integer),
    Column("max_llm_cost_month", Float),
    Column("max_drafts_month", Integer),
    Column("timezone", Text),
    Column("briefing_hour", Integer),
    Column("language", Text),
    Column("core_token_env", Text),
    Column("active", Boolean),
)

sites = Table(
    "sites",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("domain", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("language", Text),
)

sources = Table(
    "sources",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("config", JSONB),
    Column("weight", REAL),
    Column("sites", ARRAY(Text)),
    Column("site_id", BigInteger),
    Column("status", Text),
    Column("origin", Text),
    Column("evidence", JSONB),
    Column("evaluation", JSONB),
    Column("score", REAL),
    Column("evaluated_at", DateTime(timezone=True)),
    Column("status_reason", Text),
    Column("status_changed_at", DateTime(timezone=True)),
    Column("consecutive_errors", Integer),
    Column("last_item_at", DateTime(timezone=True)),
    Column("etag", Text),
    Column("last_modified", Text),
    Column("last_fetched_at", DateTime(timezone=True)),
    Column("last_error", Text),
)

source_items = Table(
    "source_items",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("source_id", BigInteger, ForeignKey("editor.sources.id"), nullable=False),
    Column("external_id", Text),
    Column("url", Text, nullable=False),
    Column("canonical_url", Text, nullable=False),
    Column("url_hash", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("summary", Text),
    Column("author", Text),
    Column("published_at", DateTime(timezone=True)),
    Column("fetched_at", DateTime(timezone=True)),
    Column("metrics", JSONB),
    Column("minhash", ARRAY(BigInteger), nullable=False),
    Column("duplicate_of", BigInteger),
    Column("content", Text),
    Column("content_fetched_at", DateTime(timezone=True)),
)

observations = Table(
    "observations",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("item_id", BigInteger, nullable=False),
    Column("observed_at", DateTime(timezone=True)),
    Column("metrics", JSONB, nullable=False),
)

topics = Table(
    "topics",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("label", Text, nullable=False),
    Column("keywords", ARRAY(Text)),
    Column("summary", Text),
    Column("labelled_by", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

topic_items = Table(
    "topic_items",
    metadata,
    _tenant(),
    Column("topic_id", BigInteger, primary_key=True),
    Column("item_id", BigInteger, primary_key=True),
)

trend_scores = Table(
    "trend_scores",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("topic_id", BigInteger, nullable=False),
    Column("computed_at", DateTime(timezone=True)),
    Column("formula_version", Text, nullable=False),
    Column("score", REAL, nullable=False),
    Column("components", JSONB, nullable=False),
    Column("missing", ARRAY(Text)),
)

llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("purpose", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost", Float),
    Column("at", DateTime(timezone=True)),
)

editorial_profiles = Table(
    "editorial_profiles",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("approved_by", Text),
    Column("approved_at", DateTime(timezone=True)),
    Column("origin", Text),
    Column("analysis_id", BigInteger),
    Column("based_on", Integer),
    Column("changes", JSONB),
)

opportunity_scores = Table(
    "opportunity_scores",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("topic_id", BigInteger, nullable=False),
    Column("site_id", BigInteger, nullable=False),
    Column("profile_id", BigInteger, nullable=False),
    Column("computed_at", DateTime(timezone=True)),
    Column("formula_version", Text, nullable=False),
    Column("score", REAL, nullable=False),
    Column("components", JSONB, nullable=False),
    Column("missing", ARRAY(Text)),
)

proposals = Table(
    "proposals",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("topic_id", BigInteger, nullable=False),
    Column("profile_id", BigInteger, nullable=False),
    Column("opportunity", REAL, nullable=False),
    Column("title", Text, nullable=False),
    Column("angle", Text),
    Column("why_now", Text),
    Column("format", Text),
    Column("generated_by", Text, nullable=False),
    Column("status", Text),
    Column("decided_by", Text),
    Column("created_at", DateTime(timezone=True)),
)

proposal_citations = Table(
    "proposal_citations",
    metadata,
    _tenant(),
    Column("proposal_id", BigInteger, primary_key=True),
    Column("item_id", BigInteger, primary_key=True),
)

briefings = Table(
    "briefings",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("briefing_date", Date, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("body_md", Text, nullable=False),
    Column("body_html", Text, nullable=False),
    Column("proposal_ids", ARRAY(BigInteger)),
    Column("sent_email_at", DateTime(timezone=True)),
    Column("sent_telegram_at", DateTime(timezone=True)),
    Column("send_error", Text),
)

recipients = Table(
    "recipients",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("channel", Text, nullable=False),
    Column("address", Text, nullable=False),
    Column("enabled", Boolean),
)

site_posts = Table(
    "site_posts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("url", Text, nullable=False),
    Column("url_hash", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("summary", Text),
    Column("categories", ARRAY(Text)),
    Column("published_at", DateTime(timezone=True)),
    Column("first_seen_at", DateTime(timezone=True)),
    Column("minhash", ARRAY(BigInteger), nullable=False),
)

site_analyses = Table(
    "site_analyses",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("problems", ARRAY(Text)),
    Column("pages_fetched", Integer),
)

profile_reviews = Table(
    "profile_reviews",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("analysis_id", BigInteger),
    Column("drift", JSONB, nullable=False),
    Column("significant", Boolean, nullable=False),
    Column("proposed_profile_id", BigInteger),
)

feedback = Table(
    "feedback",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger),
    Column("target", Text, nullable=False),
    Column("target_id", BigInteger, nullable=False),
    Column("verdict", Text, nullable=False),
    Column("note", Text),
    Column("given_by", Text, nullable=False),
    Column("given_at", DateTime(timezone=True)),
)

drafts = Table(
    "drafts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("proposal_id", BigInteger, nullable=False),
    Column("site_id", BigInteger, nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("subtitle", Text),
    Column("body_md", Text, nullable=False),
    Column("language", Text),
    Column("status", Text),
    Column("flags", JSONB),
    Column("generated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("decided_by", Text),
    Column("decided_at", DateTime(timezone=True)),
)

draft_claims = Table(
    "draft_claims",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("draft_id", BigInteger, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("section", Text),
    Column("text", Text, nullable=False),
)

draft_claim_sources = Table(
    "draft_claim_sources",
    metadata,
    _tenant(),
    Column("claim_id", BigInteger, primary_key=True),
    Column("item_id", BigInteger, primary_key=True),
)

publish_targets = Table(
    "publish_targets",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("site_id", BigInteger, nullable=False),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("config", JSONB),
    Column("secret_env", Text),
    Column("enabled", Boolean),
    Column("created_at", DateTime(timezone=True)),
)

publications = Table(
    "publications",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _tenant(),
    Column("draft_id", BigInteger, nullable=False),
    Column("target_id", BigInteger, nullable=False),
    Column("mode", Text),
    Column("payload", JSONB, nullable=False),
    Column("status", Text),
    Column("requested_by", Text, nullable=False),
    Column("requested_at", DateTime(timezone=True)),
    Column("decided_by", Text),
    Column("decided_at", DateTime(timezone=True)),
    Column("decision_note", Text),
    Column("published_at", DateTime(timezone=True)),
    Column("external_id", Text),
    Column("external_url", Text),
    Column("error", Text),
    Column("attempts", Integer),
)
