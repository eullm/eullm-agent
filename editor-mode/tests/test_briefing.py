from datetime import UTC, date, datetime

import httpx
import respx
from sqlalchemy import text

from editor import briefing, profile as prof, repo
from editor.config import Settings
from editor.schedule import Runner
from test_proposals import setup_site


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


SETTINGS = Settings(smtp_host="smtp.test", mail_from="editor@i3k.eu", telegram_token="T0K")


def add_recipients(db, tenant):
    with db.tenant(tenant) as s:
        s.execute(text("INSERT INTO editor.recipients (tenant_id, channel, address) VALUES "
                       "(:t, 'email', 'francesco@example.com'), (:t, 'telegram', '12345')"), {"t": tenant})


def test_morning_builds_and_sends_once(db, tenant):
    site_id = setup_site(db, tenant)
    with db.tenant(tenant) as s:  # a second site still waiting for approval
        other = repo.ensure_site(s, tenant, "nuovo.example", "Nuovo")
        prof.save_draft(s, tenant, other, {"domain": "nuovo.example", "subtopics": [{"name": "Cloud", "keywords": ["cloud"]}],
                                           "gaps": ["no publication dates found"], "analysis": {"status": "partial"}}, "analysis")
    add_recipients(db, tenant)
    FakeSMTP.sent = []
    with respx.mock() as router:
        tg = router.post("https://api.telegram.org/botT0K/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
        sender = briefing.Sender(SETTINGS, smtp_factory=FakeSMTP)
        runner = Runner(db, SETTINGS, http_factory=httpx.Client, core=None, sender=sender)
        day = date(2026, 10, 9)
        bid = runner.morning(tenant, day, datetime.now(UTC))
        assert tg.call_count == 1
        # A second run the same day sends nothing again.
        assert briefing.build(db, tenant, day) == bid
        again = sender.send(db, tenant, bid)
        assert again == {"email": 0, "telegram": 0, "errors": []} and tg.call_count == 1
    assert len(FakeSMTP.sent) == 1
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "francesco@example.com"
    with db.tenant(tenant) as s:
        b = s.execute(text("SELECT body_md, body_html, proposal_ids, sent_email_at, sent_telegram_at FROM editor.briefings")).one()
    assert b.sent_email_at and b.sent_telegram_at and len(b.proposal_ids) == 2
    assert "## Blog Rete (blog.example)" in b.body_md
    assert "Profilo editoriale da approvare" in b.body_md and "no publication dates found" in b.body_md
    assert "https://news.example/" in b.body_md  # proposals carry their sources
    assert "<script" not in b.body_html
    tg_text = tg.calls[0].request.content.decode()
    assert "Blog Rete" in tg_text and "parse_mode" in tg_text


def test_telegram_ok_false_is_not_marked_sent(db, tenant):
    setup_site(db, tenant)
    add_recipients(db, tenant)
    FakeSMTP.sent = []
    with respx.mock() as router:
        tg = router.post("https://api.telegram.org/botT0K/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": False, "description": "Bad Request: chat not found"}))
        sender = briefing.Sender(SETTINGS, smtp_factory=FakeSMTP)
        bid = briefing.build(db, tenant, date(2026, 10, 9))
        result = sender.send(db, tenant, bid)
        assert tg.call_count >= 1
        assert result["telegram"] == 0 and result["errors"]
    with db.tenant(tenant) as s:
        row = s.execute(text("SELECT sent_email_at, sent_telegram_at, send_error FROM editor.briefings WHERE id = :i"),
                        {"i": bid}).one()
    assert row.sent_email_at and row.sent_telegram_at is None and "telegram" in (row.send_error or "")


def test_html_is_escaped(db, tenant):
    with db.tenant(tenant) as s:
        repo.ensure_site(s, tenant, "x.example", "<script>alert(1)</script>")
    bid = briefing.build(db, tenant, date(2026, 10, 10))
    with db.tenant(tenant) as s:
        page = s.execute(text("SELECT body_html FROM editor.briefings WHERE id = :i"), {"i": bid}).scalar()
    assert "<script>alert" not in page and "&lt;script&gt;" in page


def test_tick_runs_every_tenant_and_briefs_at_eight(db, tenant):
    setup_site(db, tenant)
    runner = Runner(db, Settings(), http_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    seven_rome = datetime(2026, 10, 9, 5, tzinfo=UTC)
    eight_rome = datetime(2026, 10, 9, 6, tzinfo=UTC)
    r1 = runner.tick(seven_rome)
    assert tenant in r1.tenants and not any(x.startswith("briefing") for x in r1.tenants[tenant])
    r2 = runner.tick(eight_rome)
    assert "briefing 2026-10-09" in r2.tenants[tenant]
    r3 = runner.tick(datetime(2026, 10, 9, 7, tzinfo=UTC))
    assert not any(x.startswith("briefing") for x in r3.tenants[tenant])
