"""A small fake news site served through respx, for crawler tests."""

from __future__ import annotations

from email.utils import format_datetime
from datetime import UTC, datetime, timedelta

import httpx

BASE = "https://blog.example"
NOW = datetime.now(UTC)

ARTICLES = [
    ("ftth-aree-bianche", "Open Fiber accelera la fibra FTTH nelle aree bianche", ["Fibra"], "openfiber.it"),
    ("wifi-7-router", "Wi-Fi 7: i router che conviene comprare", ["Wi-Fi", "Router"], "wi-fi.org"),
    ("starlink-direct-to-cell", "Starlink direct to cell arriva in Italia", ["Satellite"], "starlink.com"),
    ("5g-standalone", "5G standalone: cosa cambia per gli utenti", ["Mobile"], "agcom.it"),
    ("mikrotik-routeros", "MikroTik RouterOS 7.20, novità per la fibra", ["Router"], "mikrotik.com"),
    ("velocita-fibra-2026", "Velocità della fibra in Italia: i dati di settembre", ["Fibra"], "agcom.it"),
    ("mesh-wifi-casa", "Reti mesh Wi-Fi per la casa: guida completa", ["Wi-Fi"], "wi-fi.org"),
    ("latenza-gaming", "Latenza e ping: perché contano più della velocità", ["Rete"], "openfiber.it"),
]

PARA = (
    "La connessione in fibra ottica è ormai disponibile in gran parte del paese e gli operatori "
    "stanno investendo per portare la banda ultralarga anche nelle aree meno servite, con risultati "
    "che si vedono nelle misure di velocità raccolte dagli utenti negli ultimi mesi."
)


def article_html(slug, title, cats, outbound, when):
    links = f'<a href="https://{outbound}/report">fonte</a> <a href="https://twitter.com/share">share</a>'
    return f"""<!doctype html><html lang="it"><head><title>{title} | Blog</title>
<meta property="article:published_time" content="{when.isoformat()}">
<meta property="article:section" content="{cats[0]}">
<meta name="description" content="{title}: il punto della situazione.">
<script>document.write('<p>never run this paragraph, it is inside a script tag</p>')</script>
</head><body><nav><a href="{BASE}/category/fibra/">Fibra</a></nav>
<article><h1>{title}</h1><p>{PARA}</p><p>{PARA}</p><p>Leggi la {links} per i dettagli.</p></article>
</body></html>"""


def home_html(articles=ARTICLES):
    items = "".join(f'<li><a href="{BASE}/{s}/">{t}</a></li>' for s, t, _, _ in articles)
    return f"""<!doctype html><html lang="it-IT"><head><title>Blog Rete</title>
<meta name="description" content="Notizie e guide su fibra, Wi-Fi e connettività.">
<meta property="og:site_name" content="Blog Rete">
<link rel="alternate" type="application/rss+xml" href="/feed/">
</head><body><nav class="menu"><a href="{BASE}/category/fibra/">Fibra</a>
<a href="{BASE}/category/wi-fi/">Wi-Fi</a><a href="{BASE}/category/satellite/">Satellite</a></nav>
<main><ul>{items}</ul><p>{PARA}</p></main></body></html>"""


def feed_xml(n=6, articles=ARTICLES):
    items = ""
    for i, (s, t, cats, _) in enumerate(articles[:n]):
        when = format_datetime(NOW - timedelta(days=2 * i))
        cat_xml = "".join(f"<category>{c}</category>" for c in cats)
        items += f"<item><title>{t}</title><link>{BASE}/{s}/</link><pubDate>{when}</pubDate>{cat_xml}<description>{t}.</description></item>"
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title><language>it</language>{items}</channel></rss>'


def sitemap_index():
    return f"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>{BASE}/post-sitemap.xml</loc><lastmod>{NOW.date().isoformat()}</lastmod></sitemap>
<sitemap><loc>{BASE}/page-sitemap.xml</loc></sitemap></sitemapindex>"""


def post_sitemap(articles=ARTICLES):
    urls = "".join(
        f"<url><loc>{BASE}/{s}/</loc><lastmod>{(NOW - timedelta(days=2 * i)).date().isoformat()}</lastmod></url>"
        for i, (s, *_rest) in enumerate(articles)
    )
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


ROBOTS = f"User-agent: *\nDisallow: /private/\nCrawl-delay: 0\n\nSitemap: {BASE}/sitemap_index.xml\n"


def mount(router, robots=ROBOTS, feed=True, articles=ARTICLES):
    router.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
    router.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=home_html(articles)))
    if feed:
        router.get(f"{BASE}/feed/").mock(return_value=httpx.Response(200, text=feed_xml(articles=articles)))
    router.get(f"{BASE}/sitemap_index.xml").mock(return_value=httpx.Response(200, text=sitemap_index()))
    router.get(f"{BASE}/post-sitemap.xml").mock(return_value=httpx.Response(200, text=post_sitemap(articles)))
    router.get(f"{BASE}/page-sitemap.xml").mock(return_value=httpx.Response(404))
    for i, (s, t, cats, out) in enumerate(articles):
        router.get(f"{BASE}/{s}/").mock(
            return_value=httpx.Response(200, text=article_html(s, t, cats, out, NOW - timedelta(days=2 * i)))
        )

# The same blog a few months later: security and cloud have taken over.
SHIFTED = [
    ("ransomware-ospedali", "Ransomware negli ospedali: come difendere la rete", ["Sicurezza"], "csirt.gov.it"),
    ("zero-trust-pmi", "Zero trust per le PMI: da dove partire", ["Sicurezza"], "enisa.europa.eu"),
    ("firewall-next-gen", "Firewall di nuova generazione a confronto", ["Sicurezza"], "enisa.europa.eu"),
    ("cloud-sovrano", "Cloud sovrano: cosa offrono i provider europei", ["Cloud"], "gaia-x.eu"),
    ("backup-cloud-ransomware", "Backup in cloud contro il ransomware", ["Cloud", "Sicurezza"], "csirt.gov.it"),
    ("gpu-datacenter-ai", "GPU e data center per l'AI: la rete diventa il collo di bottiglia", ["Cloud"], "gaia-x.eu"),
    ("ftth-aree-bianche", "Open Fiber accelera la fibra FTTH nelle aree bianche", ["Fibra"], "openfiber.it"),
    ("nis2-obblighi", "NIS2: gli obblighi di sicurezza per le aziende", ["Sicurezza"], "enisa.europa.eu"),
]
