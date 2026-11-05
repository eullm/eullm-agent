"""Phrases that give away a machine-written text.

The writer is told not to use them, but a prompt is not a guarantee: on
2026-11-05 an analysis of aitoday.it still found the patterns Francesco had
asked to avoid. Here they are found by rule, for free, in the article's
language; the sentences that have them are reworded where they stand
(drafts.reword), keeping their facts.

Each pattern is a phrase a journalist of the site would not write: stock
framings ("in un contesto in cui"), the negative parallelism ("non si
tratta solo di X, ma di Y"), empty weight ("rappresenta un punto di
svolta", "gioca un ruolo chiave"), stock endings ("resta da vedere",
"in conclusione"). New ones are added to the list of their language.
"""

from __future__ import annotations

import re

_IT = [
    r"non (?:si tratta|è|e') (?:solo|soltanto|semplicemente) (?:di |una? |un )?",
    r"non solo\b[^.;:]{0,80}\bma anche",
    r"in un (?:contesto|panorama|mondo|momento|periodo|quadro) (?:in cui|dove|sempre più|in continua)",
    r"in un'?(?:epoca|era) (?:in cui|di|dove)",
    r"(?:nel|un|il) panorama (?:attuale|odierno|globale|tecnologico|economico|politico|internazionale|digitale)",
    r"(?:scenario|settore|mondo|panorama) in (?:continua|rapida|costante) evoluzione",
    r"(?:rappresenta|segna|costituisce|rappresentano|segnano) (?:un|una|il|la) (?:importante |significativo |nuovo |vero |deciso )?"
    r"(?:passo|svolta|tappa|pietra miliare|punto di svolta|cambio di passo|traguardo)",
    r"punto di svolta", r"pietra miliare", r"cambio di paradigma", r"cambio di passo",
    r"(?:sottolinea|sottolineano|evidenzia|evidenziano|mette in luce|mettono in luce|testimonia|conferma) "
    r"(?:ancora una volta )?(?:l'importanza|il ruolo|la centralità|quanto|come)",
    r"si inserisc(?:e|ono) (?:in un|nel) (?:quadro|contesto|filone)", r"quadro più ampio",
    r"resta(?:no)? da (?:vedere|capire|chiarire) (?:se|come|quanto|quali|quale)",
    r"solo il tempo (?:dirà|potrà dire)", r"sarà interessante (?:vedere|capire|osservare)",
    r"\b(?:in conclusione|in sintesi|in definitiva|in ultima analisi|tirando le somme)\b",
    r"^insomma\b",
    r"(?:vale la pena|è importante|e' importante|è bene) (?:notare|sottolineare|ricordare|evidenziare)",
    r"gioca(?:no)? un ruolo (?:chiave|cruciale|fondamentale|centrale|decisivo)",
    r"\ba 360 gradi\b", r"\ba tutto tondo\b",
    r"(?:sfide e opportunità|opportunità e sfide)",
    r"\bmai come (?:oggi|ora|in questo momento)\b",
    r"(?:ma |e )?(?:cosa|che cosa) significa (?:questo|davvero|tutto (?:questo|ciò))\b",
    r"^ecco (?:cosa|perché|come|quali)\b",
    r"\bun mix di\b",
]
_EN = [
    r"\b(?:is|isn't|is not|it's not|it is not) (?:just|merely|simply|only) (?:a |an |about )",
    r"\bin (?:an|a|today's) (?:era|world|age|landscape) (?:where|of|in which)",
    r"\b(?:ever-evolving|rapidly evolving|ever-changing|evolving) landscape",
    r"\b(?:underscores|underscore|highlights|underlines) (?:the )?(?:importance|need|growing|role|significance)",
    r"\b(?:marks|represents|signals) a (?:significant |major |pivotal |new )?(?:step|shift|milestone|turning point)",
    r"\bturning point\b", r"\bpivotal\b", r"\bgame[- ]changer\b", r"\bparadigm shift\b", r"\ba testament to\b",
    r"\btapestry\b", r"\bdelv(?:e|es|ing) into\b", r"\bnavigat(?:e|es|ing) the (?:complex|challenges|landscape)",
    r"\bremains to be seen\b", r"\bonly time will tell\b",
    r"\b(?:in conclusion|in summary|all in all|to sum up)\b",
    r"\bit(?:'s| is) (?:worth noting|important to note|worth mentioning)\b",
    r"\bplays? a (?:key|crucial|pivotal|vital|central) role\b",
    r"\bwhat does (?:this|it all) (?:mean|really mean)\b",
    r"\bchallenges and opportunities\b",
]
_DE = [
    r"\bin einer welt, in der\b", r"\bin zeiten, in denen\b", r"\bes bleibt abzuwarten\b",
    r"\b(?:unterstreicht|unterstreichen) (?:die bedeutung|die rolle|einmal mehr)\b", r"\bmeilenstein\b",
    r"\b(?:zusammenfassend|abschließend lässt sich)\b", r"\bspielt eine (?:schlüsselrolle|entscheidende rolle|zentrale rolle)\b",
    r"\bparadigmenwechsel\b", r"\bwendepunkt\b", r"\bes geht nicht nur um\b",
]
_FR = [
    r"\bdans un (?:monde|contexte) où\b", r"\breste à (?:voir|savoir)\b", r"\bseul l'avenir (?:le )?dira\b",
    r"\bsouligne(?:nt)? (?:l'importance|le rôle)\b", r"\b(?:en conclusion|en somme|en définitive)\b",
    r"\bjoue(?:nt)? un rôle (?:clé|crucial|central|majeur)\b", r"\bchangement de paradigme\b",
    r"\btournant (?:majeur|décisif|historique)\b", r"\bpierre angulaire\b",
    r"\bil ne s'agit pas (?:seulement|simplement|uniquement)\b",
]
_ES = [
    r"\ben un mundo (?:donde|en el que)\b", r"\bqueda por ver\b", r"\bsolo el tiempo (?:lo )?dirá\b",
    r"\bsubraya(?:n)? la importancia\b", r"\b(?:en conclusión|en resumen|en definitiva)\b",
    r"\bjuega(?:n)? un papel (?:clave|crucial|fundamental|central)\b", r"\bcambio de paradigma\b",
    r"\bpunto de inflexión\b", r"\bno se trata (?:solo|solamente|simplemente|únicamente)\b",
]
PATTERNS = {lang: re.compile("|".join(f"(?:{p})" for p in ps), re.IGNORECASE | re.MULTILINE)
            for lang, ps in {"it": _IT, "en": _EN, "de": _DE, "fr": _FR, "es": _ES}.items()}


def stock(text: str | None, language: str | None) -> list[str]:
    """The stock phrases of machine-written text in this text, as written,
    for its language (none for a language without a list)."""
    found = PATTERNS.get((language or "").split("-")[0].lower())
    if not text or found is None:
        return []
    return list(dict.fromkeys(" ".join(x.group(0).split()) for x in found.finditer(text)))


def examples() -> str:
    """What the writer is told to avoid, in a few languages: the reworded
    sentences must not bring them back."""
    return ("\"it's not just X, it's Y\", \"in a world where\", \"marks a turning point\", \"underscores the "
            "importance\", \"plays a key role\", \"remains to be seen\", \"only time will tell\", \"a testament to\", "
            "\"landscape\", \"navigate\", \"delve\", \"challenges and opportunities\", and the same in other "
            "languages: \"non si tratta solo di\", \"in un contesto in cui\", \"rappresenta un passo importante\", "
            "\"sottolinea l'importanza\", \"gioca un ruolo chiave\", \"resta da vedere\", \"scenario in continua "
            "evoluzione\", \"a 360 gradi\", \"in conclusione\"")
