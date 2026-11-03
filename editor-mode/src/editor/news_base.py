"""The news base: general sources from around the world (world affairs,
politics, economy, technology), in English and in the languages of the
dashboard, read once an hour in the shared archive for every newsroom,
whatever sources its sites follow.

They feed the trend of the moment and its index (trends.py), and the news
the newsroom can propose (picks.py): the national news of a site's country
come from the sources in its language, the international ones from those
in English. Each feed was checked to answer before being listed here
(October and November 2026); one that stops answering is suspended by the archive's
maintenance, like any source.
"""

from __future__ import annotations

from sqlalchemy import select

from . import models as m
from . import repo

SECTIONS = ("world", "economy", "tech", "science", "culture")

# (name, feed, language, section)
BASE = [
    ("BBC News · World", "https://feeds.bbci.co.uk/news/world/rss.xml", "en", "world"),
    ("BBC News · Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "en", "economy"),
    ("BBC News · Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "en", "tech"),
    ("The Guardian · World", "https://www.theguardian.com/world/rss", "en", "world"),
    ("The Guardian · Business", "https://www.theguardian.com/uk/business/rss", "en", "economy"),
    ("The Guardian · Technology", "https://www.theguardian.com/uk/technology/rss", "en", "tech"),
    ("The New York Times · World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "en", "world"),
    ("The New York Times · Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "en", "economy"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "en", "world"),
    ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-all", "en", "world"),
    ("France 24", "https://www.france24.com/en/rss", "en", "world"),
    ("Euronews", "https://www.euronews.com/rss", "en", "world"),
    ("Euractiv", "https://www.euractiv.com/feed/", "en", "world"),
    ("Financial Times", "https://www.ft.com/rss/home", "en", "economy"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en", "economy"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "en", "tech"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "en", "tech"),
    ("TechCrunch", "https://techcrunch.com/feed/", "en", "tech"),
    ("Wired", "https://www.wired.com/feed/rss", "en", "tech"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", "en", "tech"),
    ("The Register", "https://www.theregister.com/headlines.atom", "en", "tech"),
    ("Engadget", "https://www.engadget.com/rss.xml", "en", "tech"),
    ("Hacker News", "https://hnrss.org/frontpage", "en", "tech"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/", "en", "tech"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "en", "tech"),
    # The world beyond Europe: United States, China, Japan, India, Asia, Oceania (November 2026).
    ("NPR · World", "https://feeds.npr.org/1004/rss.xml", "en", "world"),
    ("The Guardian · US", "https://www.theguardian.com/us-news/rss", "en", "world"),
    ("The Wall Street Journal · World", "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "en", "world"),
    ("The Atlantic", "https://www.theatlantic.com/feed/all/", "en", "world"),
    ("Bloomberg · Markets", "https://feeds.bloomberg.com/markets/news.rss", "en", "economy"),
    ("South China Morning Post · World", "https://www.scmp.com/rss/91/feed/", "en", "world"),
    ("South China Morning Post · China", "https://www.scmp.com/rss/4/feed/", "en", "world"),
    ("Sixth Tone", "https://api.sixthtone.com/cont/output/rssApi", "en", "world"),
    ("Xinhua", "https://english.news.cn/rss/worldrss.xml", "en", "world"),
    ("CGTN", "https://www.cgtn.com/subscribe/rss/section/world.xml", "en", "world"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", "en", "economy"),
    ("The Japan Times", "https://www.japantimes.co.jp/feed/", "en", "world"),
    ("The Hindu · International", "https://www.thehindu.com/news/international/feeder/default.rss", "en", "world"),
    ("The Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "en", "world"),
    ("The Straits Times · World", "https://www.straitstimes.com/news/world/rss.xml", "en", "world"),
    ("ABC News Australia", "https://www.abc.net.au/news/feed/51120/rss.xml", "en", "world"),
    # More of the world, science and culture, so that a story is told by many outlets (November 2026).
    ("CNN · World", "http://rss.cnn.com/rss/edition_world.rss", "en", "world"),
    ("CBS News · World", "https://www.cbsnews.com/latest/rss/world", "en", "world"),
    ("ABC News (US) · International", "https://abcnews.go.com/abcnews/internationalheadlines", "en", "world"),
    ("NBC News · World", "https://feeds.nbcnews.com/nbcnews/public/world", "en", "world"),
    ("Los Angeles Times · World & Nation", "https://www.latimes.com/world-nation/rss2.0.xml", "en", "world"),
    ("Fox News · World", "https://moxie.foxnews.com/google-publisher/world.xml", "en", "world"),
    ("Politico · Politics", "https://rss.politico.com/politics-news.xml", "en", "world"),
    ("The Hill", "https://thehill.com/news/feed/", "en", "world"),
    ("Vox", "https://www.vox.com/rss/index.xml", "en", "world"),
    ("Time", "https://time.com/feed/", "en", "world"),
    ("Sky News · World", "https://feeds.skynews.com/feeds/rss/world.xml", "en", "world"),
    ("The Telegraph", "https://www.telegraph.co.uk/news/rss.xml", "en", "world"),
    ("RTÉ · World", "https://www.rte.ie/feeds/rss/?index=/news/world/", "en", "world"),
    ("Arab News", "https://www.arabnews.com/rss.xml", "en", "world"),
    ("Yonhap", "https://en.yna.co.kr/RSS/news.xml", "en", "world"),
    ("The Diplomat", "https://thediplomat.com/feed/", "en", "world"),
    ("CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "en", "world"),
    ("Hong Kong Free Press", "https://hongkongfp.com/feed/", "en", "world"),
    ("Global Times", "https://www.globaltimes.cn/rss/outbrain.xml", "en", "world"),
    ("Taipei Times", "https://www.taipeitimes.com/xml/index.rss", "en", "world"),
    ("Dawn", "https://www.dawn.com/feeds/home", "en", "world"),
    ("AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "en", "world"),
    ("MercoPress", "https://en.mercopress.com/rss", "en", "world"),
    ("RFI · English", "https://www.rfi.fr/en/rss", "en", "world"),
    ("Meduza · English", "https://meduza.io/rss/en/all", "en", "world"),
    ("The Moscow Times", "https://www.themoscowtimes.com/rss/news", "en", "world"),
    ("Fortune", "https://fortune.com/feed/", "en", "economy"),
    ("Business Insider", "https://feeds.businessinsider.com/custom/all", "en", "economy"),
    ("The Economist · The world this week", "https://www.economist.com/the-world-this-week/rss.xml", "en", "economy"),
    ("Forbes · Business", "https://www.forbes.com/business/feed/", "en", "economy"),
    ("Investing.com", "https://www.investing.com/rss/news.rss", "en", "economy"),
    ("ZDNET", "https://www.zdnet.com/news/rss.xml", "en", "tech"),
    ("CNET", "https://www.cnet.com/rss/news/", "en", "tech"),
    ("Gizmodo", "https://gizmodo.com/rss", "en", "tech"),
    ("The Next Web", "https://thenextweb.com/feed", "en", "tech"),
    ("Tom's Hardware", "https://www.tomshardware.com/feeds/all", "en", "tech"),
    ("Rest of World", "https://restofworld.org/feed/latest/", "en", "tech"),
    ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "en", "tech"),
    ("The Decoder", "https://the-decoder.com/feed/", "en", "tech"),
    ("OpenAI News", "https://openai.com/news/rss.xml", "en", "tech"),
    ("Google · AI", "https://blog.google/technology/ai/rss/", "en", "tech"),
    ("Hugging Face · Blog", "https://huggingface.co/blog/feed.xml", "en", "tech"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/", "en", "tech"),
    ("Nature · News", "https://www.nature.com/nature.rss", "en", "science"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/all.xml", "en", "science"),
    ("New Scientist", "https://www.newscientist.com/feed/home/", "en", "science"),
    ("Space.com", "https://www.space.com/feeds/all", "en", "science"),
    ("Scientific American", "http://rss.sciam.com/ScientificAmerican-Global", "en", "science"),
    ("BBC News · Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "en", "science"),
    ("BBC News · Health", "https://feeds.bbci.co.uk/news/health/rss.xml", "en", "science"),
    ("Carbon Brief", "https://www.carbonbrief.org/feed/", "en", "science"),
    ("Inside Climate News", "https://insideclimatenews.org/feed/", "en", "science"),
    ("The Guardian · Environment", "https://www.theguardian.com/environment/rss", "en", "science"),
    ("The Guardian · Culture", "https://www.theguardian.com/culture/rss", "en", "culture"),
    ("BBC News · Entertainment & Arts", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "en", "culture"),
    ("Variety", "https://variety.com/feed/", "en", "culture"),
    ("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/", "en", "culture"),
    ("Pitchfork · News", "https://pitchfork.com/rss/news/", "en", "culture"),
    ("Sky TG24", "https://tg24.sky.it/rss/tg24.xml", "it", "world"),
    ("RaiNews", "https://www.rainews.it/rss/tutti", "it", "world"),
    ("Il Fatto Quotidiano", "https://www.ilfattoquotidiano.it/feed/", "it", "world"),
    ("AGI", "https://www.agi.it/rss", "it", "world"),
    ("Adnkronos", "https://www.adnkronos.com/RSS_Ultimora.xml", "it", "world"),
    ("La Stampa", "https://www.lastampa.it/rss/copertina.xml", "it", "world"),
    ("Internazionale", "https://www.internazionale.it/sitemaps/rss.xml", "it", "world"),
    ("Die Zeit", "https://newsfeed.zeit.de/index", "de", "world"),
    ("Süddeutsche Zeitung", "https://rss.sueddeutsche.de/rss/Topthemen", "de", "world"),
    ("DW · Deutsch", "https://rss.dw.com/xml/rss-de-all", "de", "world"),
    ("Golem", "https://rss.golem.de/rss.php?feed=RSS2.0", "de", "tech"),
    ("t3n", "https://t3n.de/rss.xml", "de", "tech"),
    ("NZZ", "https://www.nzz.ch/recent.rss", "de", "world"),
    ("Der Standard", "https://www.derstandard.at/rss", "de", "world"),
    ("Libération", "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml", "fr", "world"),
    ("franceinfo", "https://www.francetvinfo.fr/titres.rss", "fr", "world"),
    ("RFI · Français", "https://www.rfi.fr/fr/rss", "fr", "world"),
    ("01net", "https://www.01net.com/feed/", "fr", "tech"),
    ("Numerama", "https://www.numerama.com/feed/", "fr", "tech"),
    ("El Mundo", "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml", "es", "world"),
    ("ABC (España)", "https://www.abc.es/rss/2.0/portada/", "es", "world"),
    ("La Vanguardia", "https://www.lavanguardia.com/rss/home.xml", "es", "world"),
    ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/", "es", "world"),
    ("Clarín", "https://www.clarin.com/rss/lo-ultimo/", "es", "world"),
    ("ANSA", "https://www.ansa.it/sito/ansait_rss.xml", "it", "world"),
    ("ANSA · Mondo", "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml", "it", "world"),
    ("ANSA · Economia", "https://www.ansa.it/sito/notizie/economia/economia_rss.xml", "it", "economy"),
    ("ANSA · Tecnologia", "https://www.ansa.it/sito/notizie/tecnologia/tecnologia_rss.xml", "it", "tech"),
    ("Corriere della Sera", "https://xml2.corriereobjects.it/rss/homepage.xml", "it", "world"),
    ("la Repubblica", "https://www.repubblica.it/rss/homepage/rss2.0.xml", "it", "world"),
    ("Il Sole 24 Ore · Economia", "https://www.ilsole24ore.com/rss/economia.xml", "it", "economy"),
    ("Wired Italia", "https://www.wired.it/feed/rss", "it", "tech"),
    ("Punto Informatico", "https://www.punto-informatico.it/feed/", "it", "tech"),
    ("HDblog", "https://www.hdblog.it/feed/", "it", "tech"),
    ("tagesschau", "https://www.tagesschau.de/xml/rss2/", "de", "world"),
    ("Der Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss", "de", "world"),
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/schlagzeilen", "de", "economy"),
    ("Frankfurter Allgemeine", "https://www.faz.net/rss/aktuell/", "de", "world"),
    ("heise online", "https://www.heise.de/rss/heise-atom.xml", "de", "tech"),
    ("Le Monde", "https://www.lemonde.fr/rss/une.xml", "fr", "world"),
    ("France 24 · Français", "https://www.france24.com/fr/rss", "fr", "world"),
    ("Le Figaro · International", "https://www.lefigaro.fr/rss/figaro_international.xml", "fr", "world"),
    ("Le Monde · Pixels", "https://www.lemonde.fr/pixels/rss_full.xml", "fr", "tech"),
    ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "es", "world"),
    ("BBC News Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", "es", "world"),
    ("Expansión", "https://e00-expansion.uecdn.es/rss/portada.xml", "es", "economy"),
    ("Xataka", "https://www.xataka.com/feedburner.xml", "es", "tech"),
]


def ensure(db, archive: str) -> set[int]:
    """The base sources in the archive, added when missing; their ids."""
    with db.tenant(archive) as s:
        # Told apart by their config: {"base": true, ...}.
        return {repo.archive_source(s, kind="rss", name=name, url=url,
                                    config={"base": True, "language": lang, "section": section})
                for name, url, lang, section in BASE}


def sections(s, source_ids) -> dict[int, str]:
    """The section of each base source among `source_ids`."""
    src = m.sources
    rows = s.execute(select(src.c.id, src.c.config).where(src.c.id.in_(list(source_ids))))
    return {r.id: r.config["section"] for r in rows if (r.config or {}).get("base") and r.config.get("section")}
