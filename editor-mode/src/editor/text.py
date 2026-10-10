"""Tokenising for topic detection (Italian and English)."""

from __future__ import annotations

import re
from collections import Counter

from .normalize import normalize_text

STOPWORDS = set(
    """
    a about above after again against all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers him his how i if in into is it its itself just me more most my
    new no nor not now of off on once only or other our out over own same she should so some such
    than that the their them then there these they this those through to too under until up very
    was we were what when where which while who whom why will with would you your yours via vs
    show ask tell hn using use used how first one two get gets make makes based way ways
    il lo la i gli le un uno una di da del dello della dei degli delle al allo alla ai agli alle
    dal dallo dalla dai dagli dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle
    con per tra fra che chi cui non come dove quando anche ma ed se più piu meno molto tutto tutti
    questo questa questi queste quello quella quelli quelle sono era essere stato stata ha hanno
    abbiamo avere fa fare può puo nuovo nuova nuovi nuove ecco ora già gia oggi anni anno sempre
    sua suo suoi sue loro nostro nostra cosa cose solo dopo prima ancora così cosi verso
    """.split()
)


_SHORT_HYPHEN = re.compile(r"\b(\w{1,3})-(\w{1,3})\b")


def tokens(text: str) -> list[str]:
    """Content words; short hyphenated pairs are joined (Wi-Fi -> wifi)."""
    out = []
    for t in normalize_text(_SHORT_HYPHEN.sub(r"\1\2", text or "")).split():
        if t in STOPWORDS:
            continue
        if len(t) >= 3 or (len(t) == 2 and any(c.isdigit() for c in t)):
            out.append(t)
    return out


def item_terms(title: str, summary: str = "", summary_terms: int = 8) -> set[str]:
    """Title words plus the most frequent summary words."""
    terms = set(tokens(title))
    common = Counter(tokens(summary)).most_common(summary_terms)
    return terms | {t for t, _ in common}


def top_terms(texts: list[str], n: int = 12) -> list[str]:
    # Ties keep the order of first appearance, so the same titles always give
    # the same terms (a set would follow the per-process string hash).
    counts = Counter()
    for t in texts:
        counts.update(dict.fromkeys(tokens(t), 1))
    return [t for t, _ in counts.most_common(n)]
