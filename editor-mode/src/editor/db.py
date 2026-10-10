"""Database access. Every query runs inside a tenant scope: the tenant id is
set with SET LOCAL and row level security does the filtering, so a missing
WHERE clause cannot leak another tenant's rows."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import sqlalchemy_url


def make_engine(url: str, **kwargs) -> Engine:
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True, **kwargs)


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._factory = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> "Database":
        return cls(make_engine(url))

    @contextmanager
    def tenant(self, tenant_id: str) -> Iterator[Session]:
        """A transaction scoped to one tenant; commits on success."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        with self._factory() as session, session.begin():
            session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            yield session
