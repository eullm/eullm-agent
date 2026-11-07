# Un template su misura per Redazia

Questa guida è per chi costruisce il proprio sito o template e vuole che
Redazia ci pubblichi senza adattamenti. È scritta per gli sviluppatori.
Per WordPress e per i modelli Redazia basta [la guida per i
clienti](sito-pronto-per-redazia.md).

Redazia non chiede API particolari al sito: usa uno di tre canali standard.
Il template deve solo rispettare il contratto del canale scelto.

| Canale | Come arriva l'articolo | Adatto a |
|---|---|---|
| **GitHub** | un commit con un file Markdown nel repository | siti statici: Astro, Hugo, Eleventy, Next, Jekyll, Gatsby |
| **Webhook** | una `POST` JSON firmata verso un tuo endpoint | CMS fatti su misura, app, headless CMS con una funzione davanti |
| **Feed** | il sito legge un JSON Feed o un RSS di Redazia | import periodici, Zapier, Make, n8n |

GitHub e webhook ricevono anche le **modifiche** e i **ritiri** decisi dal
direttore dopo la pubblicazione. Il feed contiene solo gli articoli
approvati.

---

## 1. GitHub: articoli come file Markdown

### Struttura del repository

```
src/content/articles/it/titolo-articolo.md   ← una cartella per lingua
src/content/articles/en/article-title.md
public/images/titolo-articolo.png            ← copertine
public/llms.txt                              ← facoltativo, Redazia aggiunge una riga
public/sitemap.xml                           ← solo se è un file; se la generi alla build non serve
```

- **Cartella degli articoli:** una per lingua, indicata nella destinazione
  (`path`). Se il sito ha una lingua sola basta una cartella.
- **Copertine:** in `image_path` se lo indichi, altrimenti dove le tengono
  gli articoli già presenti, altrimenti in `public/images`. Il nome è
  `{slug}.png` o `{slug}.jpg`. Se ogni articolo ha la sua cartella con
  l'immagine dentro, Redazia fa lo stesso (`{slug}/index.md` e `cover.png`).
- **Articoli già scritti:** se ce ne sono, Redazia legge gli ultimi otto e
  copia il loro formato: nomi e ordine dei campi, formato della data, `.md`
  o `.mdx`, articolo in un file o in una cartella, valori che sono uguali in
  tutti (autore, layout). Se la cartella è vuota usa il formato standard.

### Front matter standard

```yaml
---
title: "Titolo dell'articolo"
description: "Descrizione per Google, al massimo 160 caratteri"
slug: "titolo-dell-articolo"
date: 2026-11-07T08:00:00+00:00
lang: "it"
section: "Tecnologia"
keywords: ["parola chiave"]
image: "/images/titolo-dell-articolo.png"
image_alt: "Cosa mostra l'immagine"
translation_of: "slug-dell-originale"   # solo nelle traduzioni
---
```

Se il tuo template usa altri nomi, Redazia li riconosce e scrive quelli:

| Ruolo | Nomi riconosciuti |
|---|---|
| titolo | `title` |
| descrizione | `description`, `excerpt`, `summary`, `meta_description`, `subtitle` |
| data | `pubDate`, `date`, `publishDate`, `published_at`, `created`… |
| immagine | `heroImage`, `image`, `cover`, `coverImage`, `thumbnail`, `featuredImage`, `ogImage`, `banner` (anche annidata, es. `image: {src, alt}`) |
| testo alternativo | `heroImageAlt`, `imageAlt`, `image_alt`, `coverAlt`, `alt` |
| parole chiave | `tags`, `keywords` |
| lingua | `lang`, `language`, `locale` |
| rubrica | `section`, `category`, `rubrica` |

Un campo `draft` viene scritto `false`, un campo `published` viene scritto
`true`.

### Cosa deve fare il template

1. **Leggere il Markdown così com'è**, con `##` per i sottotitoli, link,
   elenchi e le note `[^1]` (GitHub Flavored Markdown; in Astro le note
   funzionano di serie, in Hugo con Goldmark). Il primo titolo `#` non c'è:
   il titolo sta nel front matter.
2. **Non chiedere campi che Redazia non scrive.** Con Astro Content
   Collections dichiara facoltativi i campi che non sono nella tabella qui
   sopra (per esempio `author`, se non è uguale in tutti gli articoli) e
   aggiungi `translation_of` come facoltativo. Non usare `.strict()`: un
   campo in più o uno obbligatorio mancante rompono la build, e la pull
   request resta ferma.
3. **Dare a ogni articolo un indirizzo stabile con lo slug**, per esempio
   `/articoli/{slug}` e `/en/articles/{slug}`. Lo stesso schema va nella
   destinazione (`url`), così Redazia conosce il link vero.
4. **Collegare le lingue con `translation_of`**: la traduzione porta lo
   slug dell'originale. Usalo per il selettore di lingua e per i tag
   `hreflang`.
5. **Mostrare la rubrica** (`section`) e avere una pagina per rubrica.
   I nomi delle rubriche devono essere uguali a quelli scritti in Redazia,
   in Sito e profilo › Redazione automatica.
6. **Ricostruire il sito a ogni commit** sul ramo principale (Vercel,
   Netlify e Cloudflare Pages lo fanno di serie).
7. **Generare alla build** sitemap, RSS e possibilmente `sitemap-news.xml`
   e dati strutturati `NewsArticle`.
8. **Con la redazione automatica**, mostrare in ogni articolo che è scritto
   dall'intelligenza artificiale (AI Act, art. 50). Redazia mette comunque
   l'etichetta in fondo al testo.

### Come Redazia pubblica, modifica e ritira

- **Pubblica:** un solo commit con l'articolo, la copertina, la riga in
  `llms.txt` e la sitemap se è un file. Un commit, un deploy. Il commit va
  su un ramo con una pull request, che Redazia unisce solo quando i
  controlli del repository sono verdi (di solito l'anteprima di Vercel o
  Netlify), aspettando fino a 15 minuti. Senza controlli la unisce subito.
  Un file con lo stesso nome e un contenuto diverso non viene mai
  sovrascritto: la pubblicazione fallisce e lo dice.
- **Modifica:** lo stesso file viene riscritto, nello stesso modo.
- **Ritira:** il file viene cancellato con la sua copertina, e la riga
  esce da `llms.txt` e dalla sitemap.

### Token

Token GitHub "fine-grained", limitato al solo repository del sito, con i
permessi **Contents: Read and write**, **Pull requests: Read and write** e
**Commit statuses: Read**.

---

## 2. Webhook: un endpoint che riceve JSON

Redazia manda una `POST` con `Content-Type: application/json` all'indirizzo
della destinazione. L'indirizzo deve essere pubblico e in HTTPS, senza
credenziali dentro; i redirect non vengono seguiti.

### Pubblicazione

```json
{
  "event": "publication",
  "mode": "publish",
  "publication_id": 42,
  "url": null,
  "title": "Titolo",
  "subtitle": "Sommario",
  "markdown": "# Titolo\n\nTesto…",
  "html": "<h1>Titolo</h1><p>Testo…</p>",
  "language": "it",
  "slug": "titolo",
  "section": "Tecnologia",
  "seo_title": "Titolo per Google",
  "meta_description": "…",
  "keyphrase": "…",
  "jsonld": [{"@type": "NewsArticle", "...": "..."}],
  "translation_of": null,
  "date": "2026-11-07T08:00:00+00:00",
  "image": {"url": "https://app.redazia.com/media/…", "alt": "…", "type": "image/png"}
}
```

- `mode`: `publish` (pubblica) o `draft` (salva come bozza).
- `translation_of`: nelle traduzioni, lo slug dell'originale.
- `image`: scarica l'immagine e salvala da te. Il link funziona, ma è
  meglio non dipendere da Redazia per servirla.
- `markdown` e `html` contengono anche il titolo come `#`/`<h1>`: se il tuo
  template mostra il titolo dal suo campo, togli la prima riga.

### Modifica e ritiro

- **`"event": "update"`**: lo stesso corpo, con lo **stesso `slug`**. Va
  riscritto lo stesso articolo. In `image` c'è la copertina attuale: un
  link diverso vuol dire una copertina nuova, `null` che è stata tolta.
- **`"event": "withdraw"`**: arrivano `publication_id`, `slug` e `url`.
  L'articolo va tolto dal sito.

### Firma

Ogni richiesta porta `X-Editor-Signature: sha256=<HMAC-SHA256 del corpo>`,
calcolato con il segreto della destinazione. Verificala sempre sul **corpo
grezzo**, prima di leggere il JSON.

Node (Express):

```js
import crypto from 'node:crypto';
app.post('/redazia', express.raw({ type: 'application/json' }), (req, res) => {
  const want = 'sha256=' + crypto.createHmac('sha256', process.env.REDAZIA_SECRET).update(req.body).digest('hex');
  const got = req.get('X-Editor-Signature') || '';
  if (got.length !== want.length || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want))) return res.sendStatus(401);
  const a = JSON.parse(req.body);
  // salva, aggiorna o togli l'articolo a.slug
  res.json({ id: a.slug, url: `https://tuosito.it/news/${a.slug}` });
});
```

PHP:

```php
$raw = file_get_contents('php://input');
$want = 'sha256=' . hash_hmac('sha256', $raw, getenv('REDAZIA_SECRET'));
if (!hash_equals($want, $_SERVER['HTTP_X_EDITOR_SIGNATURE'] ?? '')) { http_response_code(401); exit; }
$a = json_decode($raw, true);
// salva, aggiorna o togli l'articolo $a['slug']
header('Content-Type: application/json');
echo json_encode(['id' => $a['slug'], 'url' => "https://tuosito.it/news/{$a['slug']}"]);
```

Python:

```python
import hashlib, hmac, os
def valid(raw: bytes, header: str) -> bool:
    want = "sha256=" + hmac.new(os.environ["REDAZIA_SECRET"].encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header or "")
```

### Risposta

- Rispondi **2xx solo quando l'articolo è salvato**. Con un altro codice la
  pubblicazione risulta fallita in Pubblicazioni, con il motivo, e il
  direttore può riprovare.
- Se rispondi con `{"id": "...", "url": "https://..."}`, Redazia ricorda
  l'indirizzo vero dell'articolo e lo mostra nella bozza e nelle
  pubblicazioni. Senza, l'articolo risulta pubblicato ma senza link.
- Rendi l'endpoint **idempotente sullo `slug`**: se arriva due volte lo
  stesso articolo, aggiornalo invece di duplicarlo.
- Rispondi entro 30 secondi. Se il lavoro è lungo (immagini,
  ricostruzione del sito), salva e rispondi subito, poi lavora in coda.

---

## 3. Feed: il sito legge da Redazia

In Sito e profilo › "Come ricevi gli articoli" Redazia crea un feed per
sito con un indirizzo segreto:

- `https://app.redazia.com/feeds/<token>.json` (JSON Feed 1.1)
- `https://app.redazia.com/feeds/<token>.rss` (RSS 2.0)

Ogni voce del JSON Feed ha `id`, `title`, `summary`, `content_html`,
`image`, `date_published`, `language` e, sotto `_redazia`, `slug`,
`subtitle`, `markdown`, `seo_title`, `meta_description`, `keyphrase`,
`image_alt`, `sources`, `jsonld` e `translation_of`. Usa `id` per non
importare due volte lo stesso articolo.

Il feed contiene gli ultimi 50 articoli approvati e non avvisa di
modifiche e ritiri: se servono, usa il webhook.

---

## 4. Prima di andare in produzione

1. Crea la destinazione in Redazia e pubblica un articolo a mano.
2. Controlla sul sito titolo, sommario, copertina, rubrica, data, note
   delle fonti e link.
3. Pubblica la traduzione e controlla che il selettore di lingua le colleghi.
4. Modifica l'articolo da Redazia ("Aggiorna sul sito") e poi ritiralo:
   devono funzionare tutti e due.
5. Solo dopo accendi la redazione automatica.
