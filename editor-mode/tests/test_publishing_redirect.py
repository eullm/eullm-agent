"""Redirect guard for publishing (no database needed).

The publisher uses httpx with follow_redirects=False, like the Core which
re-checks every hop. A 3xx response must therefore fail closed instead of
being (mis)treated as success.
"""

import socket

import httpx
import pytest

from editor import publishing as pub


def public_resolver(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


def test_check_public_url_rejects_private_and_http():
    with pytest.raises(pub.PublishError, match="non-public"):
        pub.check_public_url("https://intranet.example", private_resolver)
    pub.check_public_url("https://blog.example", public_resolver)
    with pytest.raises(pub.PublishError, match="https"):
        pub.check_public_url("http://blog.example", public_resolver)


def test_no_redirect_passes_through():
    r = httpx.Response(201, json={"id": 1})
    pub._ensure_no_redirect(r, "https://blog.example", public_resolver)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_to_public_is_refused(status):
    r = httpx.Response(status, headers={"location": "https://cdn.example/x"})
    with pytest.raises(pub.PublishError, match="not followed"):
        pub._ensure_no_redirect(r, "https://blog.example", public_resolver)


def test_redirect_to_private_is_reported():
    r = httpx.Response(302, headers={"location": "https://internal.example/x"})
    with pytest.raises(pub.PublishError, match="non-public"):
        pub._ensure_no_redirect(r, "https://blog.example", private_resolver)


def test_relative_redirect_is_refused():
    r = httpx.Response(302, headers={"location": "/login"})
    with pytest.raises(pub.PublishError, match="not followed"):
        pub._ensure_no_redirect(r, "https://blog.example", public_resolver)
