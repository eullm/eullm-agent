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

# (name, feed, language, section, country of the outlet: ISO code, "" for one that gathers many)
BASE = [
    ("BBC News · World", "https://feeds.bbci.co.uk/news/world/rss.xml", "en", "world", "GB"),
    ("BBC News · Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "en", "economy", "GB"),
    ("BBC News · Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "en", "tech", "GB"),
    ("The Guardian · World", "https://www.theguardian.com/world/rss", "en", "world", "GB"),
    ("The Guardian · Business", "https://www.theguardian.com/uk/business/rss", "en", "economy", "GB"),
    ("The Guardian · Technology", "https://www.theguardian.com/uk/technology/rss", "en", "tech", "GB"),
    ("The New York Times · World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "en", "world", "US"),
    ("The New York Times · Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "en", "economy", "US"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "en", "world", "QA"),
    ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-all", "en", "world", "DE"),
    ("France 24", "https://www.france24.com/en/rss", "en", "world", "FR"),
    ("Euronews", "https://www.euronews.com/rss", "en", "world", "EU"),
    ("Euractiv", "https://www.euractiv.com/feed/", "en", "world", "EU"),
    ("Financial Times", "https://www.ft.com/rss/home", "en", "economy", "GB"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en", "economy", "US"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "en", "tech", "US"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "en", "tech", "US"),
    ("TechCrunch", "https://techcrunch.com/feed/", "en", "tech", "US"),
    ("Wired", "https://www.wired.com/feed/rss", "en", "tech", "US"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", "en", "tech", "US"),
    ("The Register", "https://www.theregister.com/headlines.atom", "en", "tech", "GB"),
    ("Engadget", "https://www.engadget.com/rss.xml", "en", "tech", "US"),
    ("Hacker News", "https://hnrss.org/frontpage", "en", "tech", ""),
    # What goes into hype: Hacker News stories past 100 points, Reddit's most voted posts of the
    # day (Francesco 2026-11-05). Reddit is read as a few multi-subreddit feeds, one request each,
    # because it limits how often one address may ask.
    ("Hacker News · 100+ punti", "https://hnrss.org/newest?points=100", "en", "tech", ""),
    ("Reddit · World", "https://www.reddit.com/r/worldnews+news+europe+geopolitics+UpliftingNews/top/.rss?t=day&limit=50",
     "en", "world", ""),
    ("Reddit · Tech e AI", "https://www.reddit.com/r/technology+artificial+OpenAI+LocalLLaMA+singularity+programming"
     "/top/.rss?t=day&limit=50", "en", "tech", ""),
    ("Reddit · Scienza ed economia", "https://www.reddit.com/r/science+space+Futurology+Economics+environment"
     "/top/.rss?t=day&limit=50", "en", "science", ""),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/", "en", "tech", "US"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "en", "tech", "IN"),
    # The world beyond Europe: United States, China, Japan, India, Asia, Oceania (November 2026).
    ("NPR · World", "https://feeds.npr.org/1004/rss.xml", "en", "world", "US"),
    ("Bloomberg · Markets", "https://feeds.bloomberg.com/markets/news.rss", "en", "economy", "US"),
    ("South China Morning Post · World", "https://www.scmp.com/rss/91/feed/", "en", "world", "HK"),
    ("South China Morning Post · China", "https://www.scmp.com/rss/4/feed/", "en", "world", "HK"),
    ("Sixth Tone", "https://api.sixthtone.com/cont/output/rssApi", "en", "world", "CN"),
    ("CGTN", "https://www.cgtn.com/subscribe/rss/section/world.xml", "en", "world", "CN"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", "en", "economy", "JP"),
    ("The Japan Times", "https://www.japantimes.co.jp/feed/", "en", "world", "JP"),
    ("The Hindu · International", "https://www.thehindu.com/news/international/feeder/default.rss", "en", "world", "IN"),
    ("The Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "en", "world", "IN"),
    ("The Straits Times · World", "https://www.straitstimes.com/news/world/rss.xml", "en", "world", "SG"),
    ("ABC News Australia", "https://www.abc.net.au/news/feed/51120/rss.xml", "en", "world", "AU"),
    # More of the world, science and culture, so that a story is told by many outlets (November 2026).
    ("Politico · Politics", "https://rss.politico.com/politics-news.xml", "en", "world", "US"),
    ("RTÉ · World", "https://www.rte.ie/feeds/rss/?index=/news/world/", "en", "world", "IE"),
    ("Arab News", "https://www.arabnews.com/rss.xml", "en", "world", "SA"),
    ("Yonhap", "https://en.yna.co.kr/RSS/news.xml", "en", "world", "KR"),
    ("The Diplomat", "https://thediplomat.com/feed/", "en", "world", ""),
    ("CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "en", "world", "SG"),
    ("Hong Kong Free Press", "https://hongkongfp.com/feed/", "en", "world", "HK"),
    ("Global Times", "https://www.globaltimes.cn/rss/outbrain.xml", "en", "world", "CN"),
    ("Taipei Times", "https://www.taipeitimes.com/xml/index.rss", "en", "world", "TW"),
    ("Dawn", "https://www.dawn.com/feeds/home", "en", "world", "PK"),
    ("AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "en", "world", ""),
    ("MercoPress", "https://en.mercopress.com/rss", "en", "world", "UY"),
    ("RFI · English", "https://www.rfi.fr/en/rss", "en", "world", "FR"),
    ("Meduza · English", "https://meduza.io/rss/en/all", "en", "world", "RU"),
    ("The Moscow Times", "https://www.themoscowtimes.com/rss/news", "en", "world", "RU"),
    ("The Economist · The world this week", "https://www.economist.com/the-world-this-week/rss.xml", "en", "economy", "GB"),
    ("ZDNET", "https://www.zdnet.com/news/rss.xml", "en", "tech", "US"),
    ("CNET", "https://www.cnet.com/rss/news/", "en", "tech", "US"),
    ("Gizmodo", "https://gizmodo.com/rss", "en", "tech", "US"),
    ("The Next Web", "https://thenextweb.com/feed", "en", "tech", "NL"),
    ("Tom's Hardware", "https://www.tomshardware.com/feeds/all", "en", "tech", "US"),
    ("Rest of World", "https://restofworld.org/feed/latest/", "en", "tech", ""),
    ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "en", "tech", "US"),
    ("The Decoder", "https://the-decoder.com/feed/", "en", "tech", "DE"),
    ("OpenAI News", "https://openai.com/news/rss.xml", "en", "tech", "US"),
    ("Google · AI", "https://blog.google/technology/ai/rss/", "en", "tech", "US"),
    ("Hugging Face · Blog", "https://huggingface.co/blog/feed.xml", "en", "tech", "US"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/", "en", "tech", "US"),
    ("Nature · News", "https://www.nature.com/nature.rss", "en", "science", "GB"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/all.xml", "en", "science", "US"),
    ("New Scientist", "https://www.newscientist.com/feed/home/", "en", "science", "GB"),
    ("Space.com", "https://www.space.com/feeds/all", "en", "science", "US"),
    ("Scientific American", "http://rss.sciam.com/ScientificAmerican-Global", "en", "science", "US"),
    ("BBC News · Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "en", "science", "GB"),
    ("BBC News · Health", "https://feeds.bbci.co.uk/news/health/rss.xml", "en", "science", "GB"),
    ("Carbon Brief", "https://www.carbonbrief.org/feed/", "en", "science", "GB"),
    ("Inside Climate News", "https://insideclimatenews.org/feed/", "en", "science", "US"),
    ("The Guardian · Environment", "https://www.theguardian.com/environment/rss", "en", "science", "GB"),
    ("The Guardian · Culture", "https://www.theguardian.com/culture/rss", "en", "culture", "GB"),
    ("BBC News · Entertainment & Arts", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "en", "culture", "GB"),
    ("Variety", "https://variety.com/feed/", "en", "culture", "US"),
    ("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/", "en", "culture", "US"),
    ("Pitchfork · News", "https://pitchfork.com/rss/news/", "en", "culture", "US"),
    ("Sky TG24", "https://tg24.sky.it/rss/tg24.xml", "it", "world", "IT"),
    ("RaiNews", "https://www.rainews.it/rss/tutti", "it", "world", "IT"),
    ("Il Fatto Quotidiano", "https://www.ilfattoquotidiano.it/feed/", "it", "world", "IT"),
    ("AGI", "https://www.agi.it/rss", "it", "world", "IT"),
    ("Adnkronos", "https://www.adnkronos.com/RSS_Ultimora.xml", "it", "world", "IT"),
    ("La Stampa", "https://www.lastampa.it/rss/copertina.xml", "it", "world", "IT"),
    ("Internazionale", "https://www.internazionale.it/sitemaps/rss.xml", "it", "world", "IT"),
    ("Die Zeit", "https://newsfeed.zeit.de/index", "de", "world", "DE"),
    ("Süddeutsche Zeitung", "https://rss.sueddeutsche.de/rss/Topthemen", "de", "world", "DE"),
    ("DW · Deutsch", "https://rss.dw.com/xml/rss-de-all", "de", "world", "DE"),
    ("Golem", "https://rss.golem.de/rss.php?feed=RSS2.0", "de", "tech", "DE"),
    ("t3n", "https://t3n.de/rss.xml", "de", "tech", "DE"),
    ("NZZ", "https://www.nzz.ch/recent.rss", "de", "world", "CH"),
    ("Der Standard", "https://www.derstandard.at/rss", "de", "world", "AT"),
    ("Libération", "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml", "fr", "world", "FR"),
    ("franceinfo", "https://www.francetvinfo.fr/titres.rss", "fr", "world", "FR"),
    ("RFI · Français", "https://www.rfi.fr/fr/rss", "fr", "world", "FR"),
    ("01net", "https://www.01net.com/feed/", "fr", "tech", "FR"),
    ("Numerama", "https://www.numerama.com/feed/", "fr", "tech", "FR"),
    ("El Mundo", "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml", "es", "world", "ES"),
    ("ABC (España)", "https://www.abc.es/rss/2.0/portada/", "es", "world", "ES"),
    ("La Vanguardia", "https://www.lavanguardia.com/rss/home.xml", "es", "world", "ES"),
    ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/", "es", "world", "AR"),
    ("Clarín", "https://www.clarin.com/rss/lo-ultimo/", "es", "world", "AR"),
    ("ANSA", "https://www.ansa.it/sito/ansait_rss.xml", "it", "world", "IT"),
    ("ANSA · Mondo", "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml", "it", "world", "IT"),
    ("ANSA · Economia", "https://www.ansa.it/sito/notizie/economia/economia_rss.xml", "it", "economy", "IT"),
    ("la Repubblica", "https://www.repubblica.it/rss/homepage/rss2.0.xml", "it", "world", "IT"),
    ("Il Sole 24 Ore · Economia", "https://www.ilsole24ore.com/rss/economia.xml", "it", "economy", "IT"),
    ("Punto Informatico", "https://www.punto-informatico.it/feed/", "it", "tech", "IT"),
    ("HDblog", "https://www.hdblog.it/feed/", "it", "tech", "IT"),
    ("tagesschau", "https://www.tagesschau.de/xml/rss2/", "de", "world", "DE"),
    ("Der Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss", "de", "world", "DE"),
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/schlagzeilen", "de", "economy", "DE"),
    ("Frankfurter Allgemeine", "https://www.faz.net/rss/aktuell/", "de", "world", "DE"),
    ("heise online", "https://www.heise.de/rss/heise-atom.xml", "de", "tech", "DE"),
    ("Le Monde", "https://www.lemonde.fr/rss/une.xml", "fr", "world", "FR"),
    ("France 24 · Français", "https://www.france24.com/fr/rss", "fr", "world", "FR"),
    ("Le Figaro · International", "https://www.lefigaro.fr/rss/figaro_international.xml", "fr", "world", "FR"),
    ("Le Monde · Pixels", "https://www.lemonde.fr/pixels/rss_full.xml", "fr", "tech", "FR"),
    ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "es", "world", "ES"),
    ("BBC News Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", "es", "world", "GB"),
    ("Expansión", "https://e00-expansion.uecdn.es/rss/portada.xml", "es", "economy", "ES"),
    ("Xataka", "https://www.xataka.com/feedburner.xml", "es", "tech", "ES"),
    # Italy and Europe in more voices (Francesco 2026-11-07: too many stories about the United States,
    # 55 of the 150 feeds were American). Removed the same day: feeds that still answer but stopped
    # (Corriere della Sera since 2024, CNN World, the Wall Street Journal, Xinhua, The Telegraph,
    # ANSA Tecnologia, Wired Italia).
    ("Il Messaggero", "https://www.ilmessaggero.it/rss/home.xml", "it", "world", "IT"),
    ("TGCom24", "https://www.tgcom24.mediaset.it/rss/homepage.xml", "it", "world", "IT"),
    ("la Repubblica · Esteri", "https://www.repubblica.it/rss/esteri/rss2.0.xml", "it", "world", "IT"),
    ("la Repubblica · Economia", "https://www.repubblica.it/rss/economia/rss2.0.xml", "it", "economy", "IT"),
    ("Il Sole 24 Ore · Mondo", "https://www.ilsole24ore.com/rss/mondo.xml", "it", "world", "IT"),
    ("Il Sole 24 Ore · Italia", "https://www.ilsole24ore.com/rss/italia.xml", "it", "world", "IT"),
    ("Il Sole 24 Ore · Tecnologia", "https://www.ilsole24ore.com/rss/tecnologia.xml", "it", "tech", "IT"),
    ("RaiNews · Esteri", "https://www.rainews.it/rss/esteri", "it", "world", "IT"),
    ("AGI · Estero", "https://www.agi.it/estero/rss", "it", "world", "IT"),
    ("Domani", "https://www.editorialedomani.it/rss", "it", "world", "IT"),
    ("Open", "https://www.open.online/feed/", "it", "world", "IT"),
    ("Today.it", "https://www.today.it/rss", "it", "world", "IT"),
    ("Euronews · Italiano", "https://it.euronews.com/rss", "it", "world", "EU"),
    ("Eunews", "https://www.eunews.it/feed/", "it", "world", "IT"),
    ("Der Tagesspiegel", "https://www.tagesspiegel.de/contentexport/feed/home", "de", "world", "DE"),
    ("ORF", "https://rss.orf.at/news.xml", "de", "world", "AT"),
    ("L'Express", "https://www.lexpress.fr/arc/outboundfeeds/rss/alaune.xml", "fr", "world", "FR"),
    ("20minutos", "https://www.20minutos.es/rss/", "es", "world", "ES"),
    ("elDiario.es", "https://www.eldiario.es/rss/", "es", "world", "ES"),
    ("The Irish Times", "https://www.irishtimes.com/arc/outboundfeeds/feed-irish-news/?outputType=xml", "en", "world", "IE"),
    ("TheJournal.ie", "https://www.thejournal.ie/feed/", "en", "world", "IE"),
    ("The Local Europe", "https://www.thelocal.com/feed/", "en", "world", ""),
    ("DutchNews", "https://www.dutchnews.nl/feed/", "en", "world", "NL"),
    ("Notes from Poland", "https://notesfrompoland.com/feed/", "en", "world", "PL"),
    ("LRT English", "https://www.lrt.lt/en/news-in-english?rss", "en", "world", "LT"),
    ("Balkan Insight", "https://balkaninsight.com/feed/", "en", "world", ""),
    ("The Kyiv Independent", "https://kyivindependent.com/news-archive/rss/", "en", "world", "UA"),
    # Two outlets or more for each country, in English or in the dashboard's languages (a feed in
    # Polish or Japanese would not meet the others on the same story), November 2026.
    ("The Korea Herald", "https://www.koreaherald.com/rss/newsAll", "en", "world", "KR"),
    ("The Sydney Morning Herald", "https://www.smh.com.au/rss/feed.xml", "en", "world", "AU"),
    ("The Guardian · Australia", "https://www.theguardian.com/australia-news/rss", "en", "world", "AU"),
    ("NZ Herald", "https://www.nzherald.co.nz/arc/outboundfeeds/rss/curated/78/?outputType=xml", "en", "world", "NZ"),
    ("The Globe and Mail · World", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/",
     "en", "world", "CA"),
    ("El Universal", "https://www.eluniversal.com.mx/arc/outboundfeeds/rss/?outputType=xml", "es", "world", "MX"),
    ("El Financiero", "https://www.elfinanciero.com.mx/arc/outboundfeeds/rss/?outputType=xml", "es", "economy", "MX"),
    ("El Tiempo · Mundo", "https://www.eltiempo.com/rss/mundo.xml", "es", "world", "CO"),
    ("La Tercera", "https://www.latercera.com/arc/outboundfeeds/rss/?outputType=xml", "es", "world", "CL"),
    ("El Comercio (Perú)", "https://elcomercio.pe/arcio/rss/", "es", "world", "PE"),
    ("Efecto Cocuyo", "https://efectococuyo.com/feed/", "es", "world", "VE"),
    ("The Rio Times", "https://www.riotimesonline.com/feed/", "en", "world", "BR"),
    ("Agência Brasil · Español", "https://agenciabrasil.ebc.com.br/es/rss/ultimasnoticias/feed.xml",
     "es", "world", "BR"),
    ("Daily Maverick", "https://www.dailymaverick.co.za/dmrss/", "en", "world", "ZA"),
    ("Premium Times", "https://www.premiumtimesng.com/feed", "en", "world", "NG"),
    ("Punch", "https://punchng.com/feed/", "en", "world", "NG"),
    ("Nation Africa", "https://nation.africa/kenya/rss.xml", "en", "world", "KE"),
    ("Daily Sabah · World", "https://www.dailysabah.com/rss/world", "en", "world", "TR"),
    ("The National", "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "en", "world", "AE"),
    ("Gulf News", "https://gulfnews.com/feed", "en", "world", "AE"),
    ("Saudi Gazette", "https://saudigazette.com.sa/rssFeed/74", "en", "world", "SA"),
    ("Iran International", "https://www.iranintl.com/en/feed", "en", "world", "IR"),
    ("Kyiv Post", "https://www.kyivpost.com/feed", "en", "world", "UA"),
    ("Ukrainska Pravda", "https://www.pravda.com.ua/eng/rss/", "en", "world", "UA"),
    ("Portugal Resident", "https://www.portugalresident.com/feed/", "en", "world", "PT"),
    ("Greek Reporter", "https://greekreporter.com/feed/", "en", "world", "GR"),
    ("Le Temps", "https://www.letemps.ch/articles.rss", "fr", "world", "CH"),
    ("SRF News", "https://www.srf.ch/news/bnf/rss/1646", "de", "world", "CH"),
    ("RTBF Info", "https://rss.rtbf.be/article/rss/highlight_rtbf_info.xml", "fr", "world", "BE"),
    ("Inquirer", "https://newsinfo.inquirer.net/feed", "en", "world", "PH"),
    ("Rappler", "https://www.rappler.com/feed/", "en", "world", "PH"),
    ("VnExpress International", "https://e.vnexpress.net/rss/news.rss", "en", "world", "VN"),
    ("Bangkok Post", "https://www.bangkokpost.com/rss/data/topstories.xml", "en", "world", "TH"),
    ("The Express Tribune", "https://tribune.com.pk/feed/home", "en", "world", "PK"),
    ("Die Welt", "https://www.welt.de/feeds/latest.rss", "de", "world", "DE"),
]


def ensure(db, archive: str) -> set[int]:
    """The base sources in the archive, added when missing; their ids."""
    with db.tenant(archive) as s:
        # Told apart by their config: {"base": true, ...}.
        return {repo.archive_source(s, kind="rss", name=name, url=url,
                                    config={"base": True, "language": lang, "section": section})
                for name, url, lang, section, _ in BASE}


# The country of each base feed, by its address: kept out of the source's config, which is how the
# archive finds the source again (repo.archive_source).
COUNTRY = {url: country for _, url, _, _, country in BASE if country}


def voice(source_id: int, url: str | None) -> str:
    """Who tells a story, for counting how many tell it: the country of a
    base feed (twenty American outlets on the same fact are one voice,
    Italian, French and German outlets are three; Francesco 2026-11-07), any
    other source on its own."""
    country = COUNTRY.get(url or "")
    return f"country:{country}" if country else f"source:{source_id}"


def sections(s, source_ids) -> dict[int, str]:
    """The section of each base source among `source_ids`."""
    src = m.sources
    rows = s.execute(select(src.c.id, src.c.config).where(src.c.id.in_(list(source_ids))))
    return {r.id: r.config["section"] for r in rows if (r.config or {}).get("base") and r.config.get("section")}
