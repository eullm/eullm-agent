"""Client for the EuLLM Agent Core API. Editor Mode never talks
to a model provider directly: every model call goes through the Core, which
applies the tenant's token, routing, budgets and audit."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx


class CoreError(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str
    usage: dict | None
    cost: float | None
    model: str


class CoreClient:
    def __init__(self, base_url: str, token: str, model: str = "default", client: httpx.Client | None = None,
                 guard=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.model = model
        self.http = client or httpx.Client(timeout=180)
        # Called before every model call; raises to stop it (tenant quotas).
        self.guard = guard

    def for_tenant(self, db, tenant_id: str) -> "CoreClient":
        """A client that uses the tenant's own Core token when it has one and
        stops when the tenant's monthly model budget is spent."""
        import os

        from sqlalchemy import select

        from . import models as m
        from .quotas import QuotaExceeded, check

        with db.tenant(tenant_id) as s:
            env = s.execute(select(m.tenants.c.core_token_env)).scalar()
        token = os.environ.get(env, "") if env else self.token

        def guard():
            with db.tenant(tenant_id) as s:
                try:
                    check(s, "llm_cost", adding=0)
                except QuotaExceeded as e:
                    raise CoreError(str(e)) from None

        return CoreClient(self.base_url, token or self.token, self.model, self.http, guard)

    def chat(self, messages: list[dict], model: str | None = None) -> ChatResult:
        model = model or self.model
        if self.guard is not None:
            self.guard()
        try:
            r = self.http.post(
                f"{self.base_url}/v1/llm/chat",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"model": model, "messages": messages},
            )
        except httpx.HTTPError as e:
            raise CoreError(f"Core unreachable: {e}") from e
        if r.status_code != 200:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text
            raise CoreError(f"Core returned {r.status_code}: {detail}")
        body = r.json()
        return ChatResult(body.get("content", ""), body.get("usage"), body.get("cost"), model)

    def chat_json(self, system: str, user: str, validate, model: str | None = None, attempts: int = 2):
        """Ask for a JSON answer and validate it; one retry with the error.

        Returns (value, [ChatResult...]) so the caller can record every call.
        """
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        calls = []
        last_error = "no answer"
        for _ in range(attempts):
            res = self.chat(messages, model)
            calls.append(res)
            try:
                return validate(extract_json(res.content)), calls
            except (ValueError, TypeError, KeyError) as e:
                last_error = str(e)
                messages += [
                    {"role": "assistant", "content": res.content},
                    {"role": "user", "content": f"The answer is not valid: {last_error}. Reply with the corrected JSON only."},
                ]
        raise CoreError(f"invalid JSON from model: {last_error}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str):
    """The first JSON value in a model answer, with or without a code fence."""
    m = _FENCE.search(text)
    candidate = (m.group(1) if m else text).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        start, end = candidate.find(open_), candidate.rfind(close)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found")
