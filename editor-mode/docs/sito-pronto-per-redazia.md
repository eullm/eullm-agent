# Preparare il proprio sito per Redazia

Redazia scrive gli articoli e li pubblica sul tuo sito, a mano (premi
"Pubblica" su una bozza approvata) o da sola, con la redazione automatica.
Questa guida spiega cosa deve avere il sito perché gli articoli arrivino
al posto giusto e si vedano come gli altri.

Ci sono quattro strade. Scegli la prima che vale per il tuo sito.

| Il tuo sito è… | Destinazione in Redazia | Cosa devi fare |
|---|---|---|
| WordPress | WordPress | Un utente e una password per le applicazioni |
| Statico su GitHub (Astro, Hugo, Next, Jekyll, Eleventy…) pubblicato da Vercel, Netlify o GitHub Pages | GitHub | Un token del repository e la cartella degli articoli |
| Un'altra piattaforma con un tuo programmatore | Webhook | Un indirizzo che riceve l'articolo in JSON |
| Nessuno dei precedenti | Un sito nuovo dal modello Redazia, su `news.tuodominio.it` o sotto `tuodominio.it/news` | Vedi l'ultima sezione |

Il modo più semplice è il percorso guidato: aggiungi il dominio e Redazia
ti chiede con cosa è fatto il sito (**Primi passi › Collega il sito**,
oppure `/connect`). Ti spiega dove creare la password o il token, **prova
il collegamento prima di salvare** (accesso, permessi, categorie di
WordPress, cartella e formato degli articoli su GitHub, servizio che
pubblica il sito) e solo dopo avvia l'analisi, che parte già dalle
categorie del sito: diventano le rubriche, e la rubrica di ogni articolo
è la categoria del post su WordPress.

Le destinazioni si possono anche aggiungere a mano in **Pubblicazioni**. Se il sito è in più
lingue, aggiungi una destinazione per lingua e indica la lingua: la
traduzione di un articolo va da sola alla destinazione della sua lingua.

## WordPress

Il percorso guidato (**Primi passi › Collega il sito › WordPress**) mostra
gli stessi quattro passi.

**1 · I permalink.** In wp-admin › **Impostazioni › Permalink** scegli
**Nome articolo** e salva. Redazia parla con WordPress attraverso
`https://tuosito.it/wp-json/`, che con i permalink semplici, o su server che
non leggono il `.htaccess`, risponde 404. Redazia se ne accorge e passa da
sola all'indirizzo di riserva `?rest_route=`, ma con «Nome articolo» anche
gli articoli hanno indirizzi leggibili, che piacciono di più a Google.

**2 · Un utente solo per Redazia (consigliato).** In **Utenti › Aggiungi
nuovo utente** crea per esempio `redazia`, con un'email tua e ruolo
**Editore**. È più sicuro dell'amministratore: l'Editore pubblica articoli,
carica immagini e crea categorie, ma non può installare plugin, cambiare
tema o impostazioni, né toccare gli altri utenti. Se un giorno la password
uscisse, il danno resta negli articoli, e con un utente a parte vedi subito
cosa ha scritto Redazia e puoi spegnerla da sola togliendo la sua password.
L'**Autore** non basta se vuoi che Redazia crei le rubriche: non può creare
categorie. Funziona anche con l'amministratore, ma non serve.

**3 · La password applicativa.** Entra in WordPress con l'utente del passo
2, apri **Utenti › Profilo**, scorri fino a **Password delle applicazioni**,
scrivi un nome (per esempio "Redazia") e premi **Aggiungi la password
dell'applicazione**. Copia la password che compare: WordPress la mostra una
volta sola. Non è la password con cui entri in wp-admin. Il sito deve
essere in HTTPS, altrimenti WordPress non mostra la sezione.

**4 · Prova il collegamento.** In Redazia scrivi l'indirizzo del sito, lo
**username** dell'utente del passo 2 (quello con cui entri in wp-admin,
**non** il nome dato alla password, errore frequente) e la password
applicativa. Redazia prova davvero: accesso, permessi, categorie e articoli
già pubblicati. Se l'accesso viene rifiutato con la password giusta, il
server non passa l'intestazione di accesso a WordPress: aggiungi al
`.htaccess`, sopra le regole di WordPress:

```
SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1
```

Alcuni plugin di sicurezza chiudono `/wp-json/`: va lasciato aperto per
l'utente di Redazia.

**Sito nuovo, senza articoli.** Se il sito non ha ancora articoli non c'è
niente da analizzare: dopo il collegamento Redazia ti porta a **Sito e
profilo › Redazione automatica**, dove scrivi il contesto (di cosa parla il
sito, per chi, con che tono, quali rubriche) e premi **Crea il profilo dal
contesto**. Da lì il profilo nasce come per gli altri siti.

**Le categorie.** Non devi crearle prima. Se WordPress ne ha già, diventano
le rubriche di Redazia; altrimenti le rubriche le decidi in Redazia (o le
ricava l'analisi) e alla prima uscita di ogni rubrica Redazia crea su
WordPress la categoria con lo stesso nome, o usa quella che esiste già con
quel nome. Serve il ruolo Editore o Amministratore.

Redazia invia titolo, testo, riassunto, slug, categoria e immagine di
copertina come immagine in evidenza. Con l'opzione `jsonld` aggiunge i dati
strutturati per Google, utile se non usi già Yoast o Rank Math.

## Sito statico su GitHub

Funziona con qualsiasi generatore che legge gli articoli da file Markdown
(`.md` o `.mdx`) con un'intestazione YAML tra due righe `---`.

**Cosa deve avere il repository**

- Una cartella degli articoli, per esempio `src/content/blog`. Se il sito
  è in due lingue, una cartella per lingua (`src/content/articles/it`,
  `src/content/articles/en`).
- Almeno due o tre articoli già scritti, se ci sono: Redazia li legge e
  copia il loro formato (nomi dei campi e loro ordine, formato della data,
  `.md` o `.mdx`, articolo in un file o in una cartella con l'immagine,
  dove stanno le copertine). Se la cartella è vuota usa il formato
  standard qui sotto.
- Il sito deve ricostruirsi da solo a ogni commit sul ramo principale
  (Vercel e Netlify lo fanno di serie).

**Formato standard** (quello che usa il modello Redazia)

```yaml
---
title: "Titolo dell'articolo"
description: "Descrizione per Google, al massimo 160 caratteri"
slug: "titolo-dell-articolo"
date: 2026-10-29T08:00:00Z
lang: "it"
section: "Tecnologia"
keywords: ["parola chiave", "altra"]
image: "/images/titolo-dell-articolo.webp"
image_alt: "Cosa mostra l'immagine"
---
```

Il testo segue in Markdown: sottotitoli `##`, link, elenchi, note
`[^1]` se le fonti sono mostrate come note. Il tuo modello deve mostrare il
Markdown così com'è (in Astro con `render()`, in Hugo con `.Content`).
Campi che i tuoi articoli chiamano diversamente (`pubDate`, `heroImage`,
`cover`, `category`…) vengono riconosciuti e riempiti con il nome che usi.

**Token GitHub**

Crea un token "fine-grained" su GitHub › Settings › Developer settings ›
Personal access tokens, limitato al solo repository del sito, con i
permessi **Contents: Read and write**, **Pull requests: Read and write** e
**Commit statuses: Read**. Incollalo nella destinazione.

**Destinazione in Redazia**

- Repository: `proprietario/nome`
- Cartella degli articoli: per esempio `src/content/blog`
- Indirizzo degli articoli: per esempio `https://tuosito.it/blog/{slug}`
  (serve per il link vero all'articolo, la sitemap e `llms.txt`)
- Lingua: se il sito ha più lingue

Ogni articolo è un solo commit (testo, copertina, riga in `llms.txt`,
sitemap se è un file), quindi un solo deploy. Redazia apre una pull
request e la unisce solo quando l'anteprima di Vercel o Netlify è verde:
un articolo che romperebbe il sito non arriva mai in produzione.

**Cosa conviene che il sito abbia**

- La pagina dell'articolo con titolo, data, rubrica e immagine.
- Un elenco degli ultimi articoli in home e uno per rubrica.
- Sitemap, feed RSS e `llms.txt` generati alla build.
- Il riquadro che dice che l'articolo è scritto con l'AI, se usi la
  redazione automatica (Redazia mette comunque l'etichetta nel testo).

## Webhook

Per un CMS fatto su misura o non ancora supportato. Redazia manda una
richiesta `POST` in JSON all'indirizzo che indichi, firmata con il segreto
della destinazione nell'intestazione
`X-Editor-Signature: sha256=<HMAC-SHA256 del corpo>`. Verifica sempre la
firma prima di pubblicare.

```json
{
  "event": "publication",
  "mode": "publish",
  "publication_id": 42,
  "title": "…", "subtitle": "…",
  "markdown": "…", "html": "…",
  "language": "it",
  "slug": "…", "section": "Tecnologia",
  "seo_title": "…", "meta_description": "…", "keyphrase": "…",
  "jsonld": [ … ],
  "image": {"url": "https://…", "alt": "…", "type": "image/png"}
}
```

`mode` è `publish` (pubblica) o `draft` (salva come bozza). Se il direttore
modifica l'articolo dopo la pubblicazione arriva lo stesso corpo con
`"event": "update"` (stesso `slug`: va riscritto lo stesso file o articolo),
con la copertina attuale in `image`: un link diverso vuol dire una copertina
nuova, `null` che è stata tolta;
se lo ritira arriva `"event": "withdraw"` con `publication_id` e `slug`:
l'articolo va tolto dal sito. Rispondi con
un codice 2xx quando l'articolo è salvato; con un errore la pubblicazione
risulta fallita in Pubblicazioni, con il motivo.

## Nessuna delle tre: un sito nuovo accanto al tuo

Se la tua piattaforma non permette di scrivere da fuori (Squarespace, molti
costruttori di siti), crea il blog dal **modello Redazia** e collegalo con
la destinazione GitHub. Hai due modi per metterlo sotto il tuo nome:

- **Sottodominio** `news.tuodominio.it`: un record CNAME verso Vercel o
  Netlify. È il più semplice.
- **Cartella** `tuodominio.it/news`: il tuo sito gira le richieste di
  `/news` al blog (regola di proxy su Cloudflare, Nginx, Apache, Vercel o
  Netlify). Per Google gli articoli fanno parte del tuo sito principale,
  quindi è la scelta migliore per il posizionamento. Il blog va costruito
  con `base: /news`.

## Prima di lanciare la redazione automatica

1. Pubblica un articolo a mano e controllalo sul sito: impaginazione,
   copertina, link, rubrica.
2. Se il sito è in due lingue, pubblica anche la traduzione.
3. Solo dopo accendi la redazione automatica in Sito e profilo.
