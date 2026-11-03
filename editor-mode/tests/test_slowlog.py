import logging

from fastapi.testclient import TestClient

from editor import slowlog
from editor.api import create_app


def test_a_slow_page_says_why_in_the_log(db, caplog, monkeypatch):
    monkeypatch.setattr(slowlog, "SLOW_SECONDS", 0.0)
    c = TestClient(create_app(db))
    with caplog.at_level(logging.WARNING, logger="editor.slow"):
        c.get("/login")
    assert any("slow page GET /login" in r.message for r in caplog.records)
