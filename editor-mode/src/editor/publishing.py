"""Publishing behind approval.

A publication is requested for an approved draft and a target, waits in
`pending_approval` until a person decides, and only then is executed. The
target's address must resolve to public addresses (a tenant cannot point
Editor Mode at an internal service) and credentials come from environment
variables, never from the database. Every outcome is recorded.

Adapters: WordPress REST API (as draft or published post), a signed webhook
(for automation tools that post to LinkedIn, X, newsletters...), and a
Telegram channel.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from . import models as m
from .markdown import to_html


class PublishError(RuntimeError):
    pass


def check_public_url(url: str, resolver=None) -> None:
    resolver = resolver or socket.getaddrinfo
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise PublishError("targets must use https")
    if parts.username or parts.password:
        raise PublishError("credentials in URLs are not allowed")
    host = parts.hostname or ""
    try:
        infos = resolver(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise PublishError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise PublishError(f"{host} resolves to a non-public address ({ip})")


def _secret(target) -> str:
    if not target.secret_env:
        raise PublishError(f"target {target.name} has no secret_env")
    value = os.environ.get(target.secret_env)
    if not value:
        raise PublishError(f"environment variable {target.secret_env} is not set")
    return value


def add_target(db, tenant_id: str, site_id: int, kind: str, name: str, config: dict, secret_env: str | None) -> int:
    if kind in ("wordpress", "webhook"):
        check_public_url(config.get("url", ""))
    if kind == "telegram_channel" and not str(config.get("chat_id", "")).strip():
        raise PublishError("a Telegram channel needs chat_id")
    if any(k in config for k in ("password", "token", "secret", "api_key")):
        raise PublishError("secrets go in an environment variable (secret_env), not in config")
    with db.tenant(tenant_id) as s:
        return s.execute(insert(m.publish_targets).values(
            tenant_id=tenant_id, site_id=site_id, kind=kind, name=name, config=config, secret_env=secret_env,
        ).returning(m.publish_targets.c.id)).scalar_one()


def request(db, tenant_id: str, draft_id: int, target_id: int, mode: str, by: str) -> int:
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
        t = s.execute(select(m.publish_targets).where(m.publish_targets.c.id == target_id)).first()
        if d is None or t is None:
            raise PublishError("draft or target not found")
        if d.status != "approved":
            raise PublishError("only approved drafts can be published")
        if t.site_id != d.site_id or not t.enabled:
            raise PublishError("target not available for this site")
        payload = {"title": d.title, "subtitle": d.subtitle, "markdown": d.body_md, "html": to_html(d.body_md),
                   "language": d.language}
        return s.execute(insert(m.publications).values(
            tenant_id=tenant_id, draft_id=draft_id, target_id=target_id, mode=mode, payload=payload, requested_by=by,
        ).returning(m.publications.c.id)).scalar_one()


def decide(db, tenant_id: str, pub_id: int, approve: bool, by: str, note: str | None = None) -> bool:
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.publications.c.status).where(m.publications.c.id == pub_id)).first()
        if row is None or row.status != "pending_approval":
            return False
        s.execute(update(m.publications).where(m.publications.c.id == pub_id).values(
            status="approved" if approve else "rejected", decided_by=by, decided_at=datetime.now(UTC), decision_note=note))
    return True


class Publisher:
    def __init__(self, http: httpx.Client | None = None, resolver=None, telegram_api="https://api.telegram.org"):
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.resolver = resolver
        self.telegram_api = telegram_api

    def run(self, db, tenant_id: str, pub_id: int) -> dict:
        """Execute an approved publication. Refuses anything not approved."""
        with db.tenant(tenant_id) as s:
            pub = s.execute(select(m.publications).where(m.publications.c.id == pub_id)).first()
            if pub is None:
                raise PublishError("publication not found")
            if pub.status == "published":
                return {"status": "published", "url": pub.external_url}
            if pub.status != "approved" or not pub.decided_by:
                raise PublishError(f"publication is {pub.status}: it needs a person's approval first")
            target = s.execute(select(m.publish_targets).where(m.publish_targets.c.id == pub.target_id)).first()
            cms_url = s.execute(select(m.publications.c.external_url).where(
                m.publications.c.draft_id == pub.draft_id, m.publications.c.status == "published",
                m.publications.c.external_url.is_not(None)).limit(1)).scalar()
            s.execute(update(m.publications).where(m.publications.c.id == pub_id).values(attempts=m.publications.c.attempts + 1))
        try:
            ext_id, ext_url = getattr(self, f"_{target.kind}")(target, pub, cms_url)
        except (PublishError, httpx.HTTPError, KeyError, ValueError) as e:
            msg = f"{type(e).__name__}: {e}"
            secret = os.environ.get(target.secret_env or "", "")
            if secret:  # a token can sit in the request URL (Telegram)
                msg = msg.replace(secret, "***")
            with db.tenant(tenant_id) as s:
                s.execute(update(m.publications).where(m.publications.c.id == pub_id).values(
                    status="failed", error=msg[:500]))
            return {"status": "failed", "error": msg}
        with db.tenant(tenant_id) as s:
            s.execute(update(m.publications).where(m.publications.c.id == pub_id).values(
                status="published", published_at=datetime.now(UTC), external_id=ext_id, external_url=ext_url, error=None))
        return {"status": "published", "url": ext_url}

    def _wordpress(self, target, pub, cms_url):
        base = target.config["url"].rstrip("/")
        check_public_url(base, self.resolver)
        r = self.http.post(
            f"{base}/wp-json/wp/v2/posts",
            auth=(target.config["user"], _secret(target)),
            json={"title": pub.payload["title"], "content": pub.payload["html"], "excerpt": pub.payload["subtitle"],
                  "status": "publish" if pub.mode == "publish" else "draft"},
        )
        r.raise_for_status()
        body = r.json()
        return str(body["id"]), body.get("link")

    def _webhook(self, target, pub, cms_url):
        url = target.config["url"]
        check_public_url(url, self.resolver)
        data = json.dumps({"event": "publication", "mode": pub.mode, "publication_id": pub.id,
                           "url": cms_url, **{k: pub.payload[k] for k in ("title", "subtitle", "markdown", "html", "language")}},
                          ensure_ascii=False).encode()
        sig = hmac.new(_secret(target).encode(), data, hashlib.sha256).hexdigest()
        r = self.http.post(url, content=data, headers={"Content-Type": "application/json", "X-Editor-Signature": f"sha256={sig}"})
        r.raise_for_status()
        return None, None

    def _telegram_channel(self, target, pub, cms_url):
        import html as h
        text = f"<b>{h.escape(pub.payload['title'])}</b>"
        if pub.payload.get("subtitle"):
            text += f"\n{h.escape(pub.payload['subtitle'])}"
        if cms_url:
            text += f'\n\n<a href="{h.escape(cms_url, quote=True)}">{h.escape(cms_url)}</a>'
        r = self.http.post(f"{self.telegram_api}/bot{_secret(target)}/sendMessage",
                           json={"chat_id": target.config["chat_id"], "text": text, "parse_mode": "HTML"})
        r.raise_for_status()
        return str(r.json().get("result", {}).get("message_id")), None
