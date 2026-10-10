from alembic import context
from sqlalchemy import pool, text

from editor.config import Settings
from editor.db import make_engine

config = context.config


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings.from_env().admin_database_url
    if not url:
        raise SystemExit("EDITOR_ADMIN_DATABASE_URL (or EDITOR_DATABASE_URL) is not set")
    engine = make_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS editor"))
        connection.commit()
        context.configure(
            connection=connection,
            version_table="alembic_version",
            version_table_schema="editor",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
