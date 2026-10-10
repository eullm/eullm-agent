"""Run the Alembic migrations of the editor schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import sqlalchemy_url

HERE = Path(__file__).parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)
