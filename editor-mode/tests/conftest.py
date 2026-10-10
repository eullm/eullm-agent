"""Shared fixtures. Database tests need EDITOR_TEST_DATABASE_URL (a superuser
connection to a disposable server, e.g. postgres://postgres@127.0.0.1:5432/postgres);
each test session creates its own database and drops it at the end."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from editor import migrate
from editor.db import Database

FIXTURES = Path(__file__).parent / "fixtures"
APP_PASSWORD = "editor_app_test"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _with_db(url: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if user is None:
        netloc = parts.netloc
    else:
        netloc = f"{user}:{password}@{netloc}"
    return urlunsplit((parts.scheme, netloc, "/" + dbname, parts.query, ""))


@pytest.fixture(scope="session")
def pg_urls():
    admin = os.environ.get("EDITOR_TEST_DATABASE_URL")
    if not admin:
        pytest.skip("EDITOR_TEST_DATABASE_URL not set: database tests not run")
    plain = admin.replace("postgresql+psycopg://", "postgresql://").replace("postgres://", "postgresql://")
    name = f"editor_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    admin_db = _with_db(plain, name)
    migrate.upgrade(admin_db)
    with psycopg.connect(admin_db, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE editor_app WITH LOGIN PASSWORD '{APP_PASSWORD}'")
        conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO editor_app')
    app_db = _with_db(plain, name, "editor_app", APP_PASSWORD)
    yield admin_db, app_db
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def db(pg_urls) -> Database:
    database = Database.from_url(pg_urls[1])
    yield database
    database.engine.dispose()


@pytest.fixture
def tenant(db) -> str:
    """A fresh tenant with one site."""
    from editor import repo

    tid = f"t-{uuid.uuid4().hex[:8]}"
    with db.tenant(tid) as s:
        repo.ensure_tenant(s, tid, name=tid)
    return tid
