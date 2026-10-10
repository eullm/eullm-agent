"""HTTP through the Core. Every request Editor Mode makes to a third-party
site goes through `POST /v1/fetch`, so the Core's address checks (no private
networks, every redirect re-checked), size limit, per-host pace and audit
apply to crawling and collection alike.

`CoreTransport` plugs into httpx: collectors and the crawler use a normal
`httpx.Client` and never know the difference. Only GET is possible, and only
the headers the Core accepts are forwarded.
"""

from __future__ import annotations

import base64

import httpx

FORWARDED = ("accept", "accept-language", "if-none-match", "if-modified-since")


class FetchRefused(httpx.TransportError):
    """The Core refused the address (private network, scheme, header)."""


class CoreTransport(httpx.BaseTransport):
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self.endpoint = base_url.rstrip("/") + "/v1/fetch"
        self.token = token
        self.http = client or httpx.Client(timeout=60)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise FetchRefused(f"only GET goes through the Core, not {request.method}", request=request)
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED}
        try:
            r = self.http.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.token}"},
                json={"url": str(request.url), "headers": headers},
            )
        except httpx.HTTPError as e:
            raise httpx.ConnectError(f"Core unreachable: {e}", request=request) from e
        if r.status_code == 403:
            raise FetchRefused(_error(r), request=request)
        if r.status_code != 200:
            raise httpx.ConnectError(f"Core fetch failed ({r.status_code}): {_error(r)}", request=request)
        body = r.json()
        out_headers = {
            k: body[f]
            for k, f in (("content-type", "content_type"), ("etag", "etag"), ("last-modified", "last_modified"))
            if body.get(f)
        }
        if body.get("truncated"):
            out_headers["x-truncated"] = "1"
        out_headers["x-final-url"] = body.get("url", str(request.url))
        return httpx.Response(
            body["status"],
            headers=out_headers,
            content=base64.b64decode(body.get("body_base64", "")),
            request=request,
        )


def _error(r: httpx.Response) -> str:
    try:
        return r.json().get("error", r.text)
    except ValueError:
        return r.text


def core_http_client(base_url: str, token: str, **kwargs) -> httpx.Client:
    """An httpx client whose every request goes through the Core."""
    # Redirects are followed by the Core, which checks every hop.
    return httpx.Client(transport=CoreTransport(base_url, token), follow_redirects=False, timeout=90, **kwargs)
