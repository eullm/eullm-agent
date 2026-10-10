"""Tokens for the dashboard and the API. Only the SHA-256 is stored."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import text

ROLES = ("viewer", "editor", "owner")


@dataclass(frozen=True)
class Caller:
    tenant_id: str
    name: str
    role: str

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def digest(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def new_token() -> str:
    return "edt_" + secrets.token_urlsafe(32)


def create_token(db, tenant_id: str, name: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    token = new_token()
    with db.tenant(tenant_id) as s:
        s.execute(
            text("INSERT INTO editor.access_tokens (tenant_id, name, token_sha256, role) VALUES (:t, :n, :d, :r)"),
            {"t": tenant_id, "n": name, "d": digest(token), "r": role},
        )
    return token


def resolve(db, token: str | None) -> Caller | None:
    if not token:
        return None
    with db.engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM editor.resolve_token(:d)"), {"d": digest(token)}).first()
    return Caller(row.tenant_id, row.name, row.role) if row else None
