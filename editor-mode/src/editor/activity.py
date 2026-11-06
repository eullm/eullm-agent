"""What the newsroom is doing, in plain words: the work asked for from the
dashboard (and the automatic round while it runs), the step each job is on,
how long it has been going, and what it produced once it ends, with a link
to the result. The Desk shows the day; every page shows what is running and
what has just ended."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, or_, select

from . import models as m
from .i18n import N_, t
from .queue import PRIORITY_PERSON
from .translate import LANGUAGES

KINDS = {
    "analysis": N_("Analisi del sito"),
    "discovery": N_("Ricerca delle fonti"),
    "collect": N_("Raccolta delle notizie e del trend"),
    "proposals": N_("Proposte di articoli"),
    "titles": N_("Titoli per un trend"),
    "draft": N_("Scrittura della bozza"),
    "translate": N_("Traduzione dell'articolo"),
    "image": N_("Immagine di copertina"),
    "deliver": N_("Invio dell'articolo per email"),
    "publish": N_("Pubblicazione"),
    "change": N_("Articolo sul sito"),
    "tick": N_("Giro orario automatico"),
    "auto": N_("Redazione automatica"),
    "brief": N_("Profilo dal contesto"),
    "evening": N_("Il Punto della Sera"),
    "request": N_("Articolo richiesto"),
    "wpmenu": N_("Menu del sito WordPress"),
}
RECENT = timedelta(minutes=15)  # how long an ended job stays on every page
STALLED = timedelta(minutes=3)  # queued this long with nothing running: the worker is not taking work


def _duration(d: timedelta) -> str:
    sec = max(0, int(d.total_seconds()))
    if sec < 60:
        return t("{n} s", n=sec)
    if sec < 3600:
        return t("{n} min", n=sec // 60)
    return t("{h} h {m} min", h=sec // 3600, m=sec % 3600 // 60)


def _site_link(site_id, back: str) -> str:
    return f"/switch?site={site_id}&back={quote(back, safe='')}" if site_id else back


def _result(kind: str, r: dict, site_id, humanize: Callable[[str], str]) -> tuple[list[str], str | None, str | None]:
    """Sentences on what the job produced, and a link to it."""
    if kind == "evening":
        if r.get("skipped"):
            return [t("Il Punto della Sera non è stato scritto: oggi gli articoli pubblicati sono troppo pochi.")
                    if r["skipped"] == "few articles" else humanize(r["skipped"])], _site_link(site_id, "/site?tab=auto"), \
                t("Impostazioni")
        if not r.get("discarded") and not r.get("held") and not r.get("target") and not r.get("approved"):
            return [t("Il Punto della Sera è pronto da rileggere: «{title}».", title=r.get("title", ""))], \
                f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts", t("Leggi la bozza")
        kind = "auto"
    if kind == "analysis":
        if r.get("status") == "failed":
            return [t("Il sito non si è potuto leggere.")] + [humanize(p) for p in r.get("problems") or []], \
                _site_link(site_id, "/site"), t("Vedi i dettagli")
        out = []
        if r.get("profile_id") and r.get("changes"):
            out.append(t("Il sito è cambiato: nuova versione del profilo da approvare ({n} differenze).", n=r["changes"]))
        elif r.get("profile_id") and "incomplete" in r:
            out.append(t("Profilo da approvare, ma incompleto: il modello non ha ricavato settore, pubblico e stile. "
                         "Riprovo da solo entro un'ora; puoi anche completarli tu."))
            if r["incomplete"]:
                out.append(t("Motivo: {why}", why=humanize(r["incomplete"])))
        elif "incomplete" in r:
            out.append(t("Il modello non ha risposto: la linea approvata resta quella in uso. Riprovo da solo entro un'ora."))
            if r["incomplete"]:
                out.append(t("Motivo: {why}", why=humanize(r["incomplete"])))
        elif r.get("profile_id"):
            out.append(t("Profilo pronto da approvare."))
        else:
            out.append(t("Nessun cambiamento: la linea del sito è la stessa del profilo in uso."))
        src = r.get("sources") or {}
        if src.get("no_profile"):
            out.append(t("Le fonti si cercano dopo l'approvazione del profilo."))
        elif src:
            out.append(t("Fonti: {n} provate, {a} attivate, {c} da valutare.", n=src.get("candidates", 0),
                         a=src.get("active", 0), c=src.get("candidate", 0)))
        return out, _site_link(site_id, "/site"), t("Vedi il profilo")
    if kind == "discovery":
        if r.get("no_profile"):
            return [t("Prima approva il profilo: le fonti si cercano a partire dai suoi temi.")], \
                _site_link(site_id, "/site"), t("Vedi il profilo")
        why = [t("Note: {notes}", notes="; ".join(r["notes"]))] if r.get("notes") else []
        if not r.get("candidates"):
            return [t("Nessuna fonte nuova da provare: quelle trovate sono già nell'elenco.")] + why, \
                _site_link(site_id, "/sources?status=all"), t("Vedi le fonti")
        return [t("{n} fonti provate: {a} attivate, {c} da valutare, {x} scartate.", n=r["candidates"],
                  a=r.get("active", 0), c=r.get("candidate", 0), x=r.get("rejected", 0) + r.get("failed", 0))] + why, \
            _site_link(site_id, "/sources?status=candidate" if r.get("candidate") else "/sources"), t("Vedi le fonti")
    if kind == "collect":
        out = [t("{s} fonti lette, {n} notizie nuove, {k} temi nuovi.", s=r.get("sources", 0), n=r.get("new", 0),
                 k=r.get("new_topics", 0))]
        if r.get("outage"):
            out.append(t("{n} fonti su {s} non hanno risposto insieme: il guasto è dalla nostra parte (rete del "
                         "server o Core), non nei siti. Non contano per la sospensione e riprovano al prossimo giro.",
                         n=r["errors"], s=r.get("sources", 0)))
        elif r.get("errors"):
            out.append(t("{n} fonti non hanno risposto: riprovano al prossimo giro.", n=r["errors"]))
        return out, "/trends", t("Vedi i trend")
    if kind in ("proposals", "titles"):
        if r.get("proposals"):
            return [t("{n} proposte nuove da decidere.", n=r["proposals"])], "/", t("Decidi")
        why = r.get("why")
        if why == "no_news":
            return [t("Nessuna notizia raccolta nelle ultime 72 ore: la raccolta è ferma o le fonti non rispondono. "
                      "Lancia la raccolta dal Desk e controlla le fonti.")], "/sources", t("Vedi le fonti")
        if why == "no_profile":
            return [t("Prima approva il profilo del sito.")], _site_link(site_id, "/site"), t("Vedi il profilo")
        if why == "all_proposed":
            return [t("Tutte le notizie delle ultime 72 ore sono già state proposte a questo sito: "
                      "le prossime arrivano con la raccolta, ogni ora.")], _site_link(site_id, "/trends"), t("Vedi i trend")
        return [t("Nessuna proposta nuova.")], _site_link(site_id, "/trends"), t("Vedi i trend")
    if kind == "wpmenu":
        if r.get("error") == "no_sections":
            return [t("Il sito non ha rubriche: scrivile in Redazione automatica › Regole.")], \
                _site_link(site_id, "/site?tab=auto"), t("Rubriche")
        if r.get("error") == "no_wordpress":
            return [t("Il sito non ha una destinazione WordPress.")], "/connect", t("Collega il sito")
        out = []
        for x in r.get("sites") or []:
            if x.get("error") == "role":
                line = t("{name}: categorie pronte ({n}), ma l'utente collegato non può cambiare i menu: su WordPress lo "
                         "può fare solo un amministratore. Crea il menu da Aspetto › Menu, oppure collega un utente "
                         "amministratore.", name=x["name"], n=x.get("categories", 0))
            elif x.get("error") == "block_theme":
                line = t("{name}: categorie pronte ({n}), ma il tema usa la navigazione a blocchi, senza posizioni per i "
                         "menu: aggiungi le categorie da Aspetto › Editor › Navigazione.", name=x["name"],
                         n=x.get("categories", 0))
            else:
                line = t("{name}: menu aggiornato (voci aggiunte: {a}, già presenti: {p}).", name=x["name"],
                         a=len(x.get("added") or []), p=x.get("present", 0))
            if x.get("missing"):
                line += " " + t("Categorie non create (serve un utente che possa crearle): {names}.",
                                names=", ".join(x["missing"]))
            out.append(line)
        return out, _site_link(site_id, "/site?tab=auto"), t("Rubriche")
    if kind == "request":
        unread = r.get("unread") or []
        missed = [t("Link non letti (pagina vuota, a pagamento o che non si lascia leggere): {links}",
                    links=", ".join(unread))] if unread else []
        if r.get("skipped"):
            return [humanize(r["skipped"])], "/plan", t("Il mio piano")
        if r.get("no_profile"):
            return [t("Prima approva il profilo del sito.")], _site_link(site_id, "/site"), t("Vedi il profilo")
        if r.get("none"):
            return [t("Nessuna bozza: nell'archivio delle ultime due settimane non ci sono notizie sull'argomento "
                      "e nessun link si è potuto leggere. Ogni frase deve avere una fonte, quindi la redazione non "
                      "scrive a vuoto: aggiungi uno o più link, oppure usa parole più vicine ai titoli delle notizie.")] \
                + missed, "/", t("Riprova dal Desk")
        text = t("Bozza pronta: «{title}».", title=r.get("title", ""))
        if r.get("status") == "needs_review":
            text += " " + t("Alcuni controlli non sono passati: rileggila con attenzione.")
        return [text, t("Scritta da {l} link e {n} notizie dell'archivio.", l=r.get("links", 0), n=r.get("found", 0))] \
            + missed, f"/drafts/{r['draft_id']}", t("Leggi la bozza")
    if kind == "draft":
        text = t("Bozza pronta: «{title}».", title=r.get("title", ""))
        if r.get("status") == "needs_review":
            text += " " + t("Alcuni controlli non sono passati: rileggila con attenzione.")
        return [text], f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts", t("Leggi la bozza")
    if kind == "auto":
        link = f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts"
        if r.get("skipped"):
            return [t("Non scritto dalla redazione automatica: «{title}». Non è costato niente e un'altra proposta "
                      "prende il suo posto.", title=r.get("title", "")),
                    t("Motivi: {why}", why="; ".join(humanize(x) for x in r["skipped"]))], "/proposals", t("Le proposte")
        if r.get("discarded"):
            return [t("Scartato dalla redazione automatica: «{title}». Non è stato pubblicato e non è servita "
                      "nessuna copertina.", title=r.get("title", "")),
                    t("Motivi: {why}", why="; ".join(humanize(x) for x in r["discarded"]))], link, t("Leggi la bozza")
        if r.get("held"):
            return [t("Fermato dal caporedattore AI: «{title}». Resta una bozza da rileggere.", title=r.get("title", "")),
                    t("Motivi: {why}", why="; ".join(humanize(x) for x in r["held"]))], link, t("Leggi la bozza")
        out = []
        if r.get("corrected"):
            out.append(t("Il caporedattore AI l'ha corretto invece di scartarlo: {why}",
                         why="; ".join(humanize(x) for x in r["corrected"])))
        if r.get("rewritten_from"):
            out.append(t("La prima versione non passava: riscritto dal modello più forte."))
        out += [t("«{title}» approvato dal caporedattore AI e inviato a {target}.", title=r.get("title", ""),
                 target=r["target"]) if r.get("target")
               else t("«{title}» approvato dal caporedattore AI: nessuna destinazione per pubblicarlo, è nel feed e "
                      "nel pacchetto.", title=r.get("title", ""))]
        if r.get("translations"):
            out.append(t("Traduzioni in arrivo: {languages}.",
                         languages=", ".join(LANGUAGES.get(x, x) for x in r["translations"])))
        return out, link, t("Apri l'articolo")
    if kind == "brief":
        return [t("Profilo scritto dal contesto (versione {version}): controllalo e approvalo.", version=r.get("version"))], \
            _site_link(site_id, "/site#h-profile"), t("Vedi il profilo")
    if kind == "translate" and r.get("discarded"):
        return [t("Traduzione scartata: «{title}». L'originale resta pubblicato.", title=r.get("title", "")),
                t("Motivi: {why}", why="; ".join(humanize(x) for x in r["discarded"]))], \
            f"/drafts/{r['draft_id']}", t("Leggi la traduzione")
    if kind == "translate" and r.get("held"):
        return [t("Traduzione fermata: «{title}».", title=r.get("title", "")),
                t("Motivi: {why}", why="; ".join(humanize(x) for x in r["held"]))], \
            f"/drafts/{r['draft_id']}", t("Leggi la traduzione")
    if kind == "translate" and r.get("target"):
        return [t("Traduzione in {language} inviata a {target}: «{title}».",
                  language=LANGUAGES.get(r.get("language"), r.get("language")), target=r["target"],
                  title=r.get("title", ""))], f"/drafts/{r['draft_id']}", t("Leggi la traduzione")
    if kind == "translate":
        text = t("Traduzione in {language} pronta: «{title}».", language=LANGUAGES.get(r.get("language"), r.get("language")),
                 title=r.get("title", ""))
        if r.get("status") == "needs_review":
            text += " " + t("Alcuni controlli non sono passati: rileggila con attenzione.")
        return [text], f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts", t("Leggi la traduzione")
    if kind == "image":
        out = [t("Immagine pronta per «{title}».", title=r.get("title", ""))]
        if r.get("online"):
            out.append(t("L'articolo è già online: per mettere anche lì la nuova copertina premi «Aggiorna sul sito» "
                         "nella sua pagina (e in quella di ogni traduzione pubblicata)."))
        return out, f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts", t("Vedi l'immagine")
    if kind == "deliver":
        link = f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/drafts"
        if not r.get("sent"):
            return [t("Nessun destinatario email: aggiungilo in Impostazioni per ricevere gli articoli approvati.")], \
                "/settings", t("Impostazioni")
        return [t("«{title}» inviato per email a {n} destinatari, con il pacchetto da pubblicare.",
                  title=r.get("title", ""), n=r["sent"])], link, t("Apri l'articolo")
    if kind == "publish":
        out = [t("Pubblicata.")]
        if r.get("indexnow") == "sent":
            out.append(t("Segnalata subito a Bing con IndexNow."))
        elif r.get("indexnow"):
            out.append(t("Segnalazione IndexNow non riuscita: la pagina resta pubblicata."))
        return out, r.get("url") or "/publications", t("Apri")
    if kind == "change":
        if r.get("op") == "withdraw":
            return [t("Ritirato dal sito: «{title}».", title=r.get("title", ""))], \
                f"/drafts/{r['draft_id']}" if r.get("draft_id") else "/publications", t("Apri l'articolo")
        return [t("Aggiornato sul sito: «{title}».", title=r.get("title", ""))], r.get("url") or "/publications", t("Apri")
    return [], None, None


SHORT = 5  # jobs the compact box shows; the rest are on the Attività page


def recent(s, tz, humanize: Callable[[str], str], full: bool = False, now: datetime | None = None,
           compact: bool = True, site: int | None = None) -> dict:
    """The jobs to show: those waiting or running, and those ended in the
    last 15 minutes (the last day on the Desk and on the Attività page).
    `compact`: one line per job, at most five on the Desk; the Attività
    page shows every job with all it produced. `site`: only that site's
    jobs, and those of every site (collection)."""
    now = now or datetime.now(UTC)
    j = m.jobs.c
    since = now - (timedelta(hours=24) if full else RECENT)
    rows = s.execute(select(m.jobs).where(
        j.kind.in_(KINDS), j.priority.is_not(None),
        or_(j.priority >= PRIORITY_PERSON, j.status == "running"),
        or_(j.status.in_(("queued", "running")), j.finished_at >= since),
    ).order_by(j.id.desc()).limit((30 if compact else 100) if full else 8)).all()
    domains = dict(s.execute(select(m.sites.c.id, m.sites.c.domain)).all())
    by_domain = {d: i for i, d in domains.items()}
    proposal_ids = [r.args.get("proposal_id") for r in rows if r.kind in ("draft", "auto") and r.args]
    proposal_rows = s.execute(select(m.proposals.c.id, m.proposals.c.site_id, m.proposals.c.title)
                              .where(m.proposals.c.id.in_([x for x in proposal_ids if x]))).all() if proposal_ids else []
    proposal_site = {p.id: p.site_id for p in proposal_rows}
    # The article each job is about, so a person who asked for it knows which one is running.
    proposal_title = {p.id: p.title for p in proposal_rows}
    draft_ids = [r.args.get("draft_id") for r in rows if r.kind in ("translate", "image") and r.args]
    draft_title = dict(s.execute(select(m.drafts.c.id, m.drafts.c.title).where(
        m.drafts.c.id.in_([x for x in draft_ids if x]))).all()) if draft_ids else {}
    # Something running or just ended, among all the jobs and not only those
    # shown: a long queue pushes the running ones out of the list, and the
    # worker is not stalled because of that.
    running = any(r.status == "running" for r in rows) or s.execute(select(func.count()).select_from(m.jobs).where(
        or_(j.status == "running", j.finished_at >= now - STALLED))).scalar() > 0
    items, stalled = [], False
    for r in rows:
        args, res = r.args or {}, r.result or {}
        site_id = res.get("site_id") or args.get("site_id") or by_domain.get(r.subject) \
            or proposal_site.get(args.get("proposal_id"))
        if site is not None and site_id != site and r.kind not in ("collect", "tick"):
            continue
        where = domains.get(site_id) or (r.subject if r.kind == "analysis" else None)
        if r.kind in ("collect", "tick"):
            where = t("tutti i siti")
        step = None
        if r.progress and r.progress.get("text"):
            try:
                step = t(r.progress["text"], **(r.progress.get("values") or {}))
            except (KeyError, IndexError, ValueError):
                step = r.progress["text"]
        title = res.get("title") or proposal_title.get(args.get("proposal_id")) or draft_title.get(args.get("draft_id")) \
            or ((args.get("text") or "")[:120] if r.kind == "request" else None)
        item = {"id": r.id, "what": t(KINDS[r.kind]), "where": where, "status": r.status, "step": step,
                "title": title if r.kind in ("draft", "auto", "translate", "image", "request") else None,
                "lines": [], "href": None, "link": None, "warn": None,
                # The hour it ended, or since when it waits or runs: the compact box's last column.
                "at": _aware(r.finished_at or r.started_at or r.run_after, now).astimezone(tz).strftime("%H:%M")}
        if r.status == "queued":
            waited = now - _aware(r.run_after or r.started_at, now)
            item["state"] = t("In coda da {d}", d=_duration(waited))
            if r.error and r.run_after and _aware(r.run_after, now) > now:
                # Stopped by a model provider that is down: it runs again by itself.
                item["state"] = t("Il modello non risponde: riprovo da solo alle {time}",
                                  time=_aware(r.run_after, now).astimezone(tz).strftime("%H:%M"))
                item["lines"] = [humanize(r.error)]
            if waited > STALLED and not running:
                stalled = True
        elif r.status == "running":
            item["state"] = t("In corso da {d}", d=_duration(now - _aware(r.started_at, now)))
        elif r.status == "done":
            item["state"] = t("Finito alle {time}, in {d}", time=_aware(r.finished_at, now).astimezone(tz).strftime("%H:%M"),
                              d=_duration(_aware(r.finished_at, now) - _aware(r.started_at, now)))
            item["lines"], item["href"], item["link"] = _result(r.kind, res, site_id, humanize)
            item["step"] = None
            if r.kind == "analysis" and res.get("status") == "failed":
                item["status"] = "failed"  # the work ran, but the site could not be read
            elif r.kind == "proposals" and res.get("why") == "no_news":
                item["status"] = "failed"  # nothing came in: the collection is stopped
            elif "incomplete" in res:
                item["status"] = "partial"
        else:
            item["state"] = t("Non riuscito alle {time}", time=_aware(r.finished_at or now, now).astimezone(tz).strftime("%H:%M"))
            item["lines"] = [humanize(r.error or "")]
            if step:
                item["lines"].append(t("Era a questo passo: {step}", step=step))
            item["step"] = None
        items.append(item)
    items.sort(key=lambda x: (x["status"] not in ("running", "queued"), x["status"] != "running"))
    total = len(items)
    if compact and full:
        # The Desk keeps what is running and the latest ones; the rest is a click away.
        items = items[:max(SHORT, sum(x["status"] in ("running", "queued") for x in items))]
    return {"items": items, "active": any(x["status"] in ("running", "queued") for x in items),
            "stalled": stalled, "full": full, "compact": compact, "total": total,
            "mode": ("desk" if compact else "page") if full else "recent"}


def _aware(value: datetime | None, now: datetime) -> datetime:
    if value is None:
        return now
    return value if value.tzinfo else value.replace(tzinfo=UTC)
