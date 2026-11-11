/* Redazia: feedback from readers, under an article.
   <script src="https://app.redazia.com/static/feedback.js" defer></script>
   Optional: data-lang="it|en|de|fr|es", data-privacy="/privacy", and a
   <div id="redazia-feedback"></div> where the form should go (else it goes
   where the script is). The message reaches only the newsroom, after the
   reader confirms it from the email; it is never published. */
(function () {
  "use strict";
  var me = document.currentScript;
  if (!me || !window.fetch) return;
  var base = new URL(me.src).origin;
  var api = base + "/api/public/feedback";
  var T = {
    it: { title: "Hai notato un errore o vuoi scrivere alla redazione?", kind: "Tipo", error: "Segnala un errore",
      comment: "Un commento per la redazione", other: "Altro", message: "Messaggio", email: "La tua email",
      send: "Invia", note: "Il messaggio arriva solo alla redazione e non viene pubblicato. Ti mandiamo un'email per confermarlo: l'indirizzo serve solo per risponderti.",
      privacy: "Privacy", sent: "Fatto. Ora apri l'email che ti abbiamo mandato e conferma: senza conferma il messaggio viene cancellato entro 48 ore.",
      e_email: "Controlla l'indirizzo email.", e_length: "Scrivi tra 10 e 2000 caratteri.",
      e_pending: "Hai già un messaggio da confermare: apri l'email che ti abbiamo mandato.",
      e_limit: "Troppi messaggi: riprova più tardi.", e_captcha: "Verifica non riuscita: riprova.",
      e_other: "Non è stato possibile inviare il messaggio: riprova più tardi." },
    en: { title: "Spotted a mistake, or want to write to the newsroom?", kind: "Type", error: "Report a mistake",
      comment: "A comment for the newsroom", other: "Other", message: "Message", email: "Your email",
      send: "Send", note: "Your message reaches only the newsroom and is never published. We will email you to confirm it: your address is only used to reply.",
      privacy: "Privacy", sent: "Done. Now open the email we sent you and confirm: unconfirmed messages are deleted within 48 hours.",
      e_email: "Please check your email address.", e_length: "Write between 10 and 2000 characters.",
      e_pending: "You already have a message to confirm: open the email we sent you.",
      e_limit: "Too many messages: try again later.", e_captcha: "Verification failed: try again.",
      e_other: "The message could not be sent: try again later." },
    de: { title: "Einen Fehler gefunden oder der Redaktion schreiben?", kind: "Art", error: "Fehler melden",
      comment: "Ein Kommentar für die Redaktion", other: "Sonstiges", message: "Nachricht", email: "Deine E-Mail",
      send: "Senden", note: "Die Nachricht geht nur an die Redaktion und wird nicht veröffentlicht. Wir schicken dir eine E-Mail zur Bestätigung: die Adresse dient nur der Antwort.",
      privacy: "Datenschutz", sent: "Fertig. Öffne jetzt die E-Mail und bestätige: unbestätigte Nachrichten werden nach 48 Stunden gelöscht.",
      e_email: "Bitte prüfe die E-Mail-Adresse.", e_length: "Schreibe zwischen 10 und 2000 Zeichen.",
      e_pending: "Du hast schon eine Nachricht zu bestätigen: öffne die E-Mail, die wir dir geschickt haben.",
      e_limit: "Zu viele Nachrichten: versuche es später.", e_captcha: "Prüfung fehlgeschlagen: versuche es erneut.",
      e_other: "Die Nachricht konnte nicht gesendet werden: versuche es später." },
    fr: { title: "Vous avez repéré une erreur ou voulez écrire à la rédaction ?", kind: "Type", error: "Signaler une erreur",
      comment: "Un commentaire pour la rédaction", other: "Autre", message: "Message", email: "Votre e-mail",
      send: "Envoyer", note: "Le message n'arrive qu'à la rédaction et n'est jamais publié. Nous vous envoyons un e-mail pour le confirmer : l'adresse sert seulement à vous répondre.",
      privacy: "Confidentialité", sent: "C'est fait. Ouvrez l'e-mail que nous vous avons envoyé et confirmez : sans confirmation le message est effacé sous 48 heures.",
      e_email: "Vérifiez l'adresse e-mail.", e_length: "Écrivez entre 10 et 2000 caractères.",
      e_pending: "Vous avez déjà un message à confirmer : ouvrez l'e-mail que nous vous avons envoyé.",
      e_limit: "Trop de messages : réessayez plus tard.", e_captcha: "Vérification échouée : réessayez.",
      e_other: "Le message n'a pas pu être envoyé : réessayez plus tard." },
    es: { title: "¿Has visto un error o quieres escribir a la redacción?", kind: "Tipo", error: "Señalar un error",
      comment: "Un comentario para la redacción", other: "Otro", message: "Mensaje", email: "Tu email",
      send: "Enviar", note: "El mensaje llega solo a la redacción y no se publica. Te enviamos un email para confirmarlo: la dirección sirve solo para responderte.",
      privacy: "Privacidad", sent: "Hecho. Abre el email que te hemos enviado y confirma: sin confirmación el mensaje se borra en 48 horas.",
      e_email: "Revisa la dirección de email.", e_length: "Escribe entre 10 y 2000 caracteres.",
      e_pending: "Ya tienes un mensaje por confirmar: abre el email que te hemos enviado.",
      e_limit: "Demasiados mensajes: inténtalo más tarde.", e_captcha: "Verificación fallida: inténtalo de nuevo.",
      e_other: "No se ha podido enviar el mensaje: inténtalo más tarde." }
  };

  function el(tag, attrs, text) {
    var x = document.createElement(tag);
    for (var k in attrs || {}) x.setAttribute(k, attrs[k]);
    if (text) x.textContent = text;
    return x;
  }

  var CSS = ".rzf{margin:2.5rem 0 1rem;padding:1.25rem;border:1px solid rgba(127,127,127,.35);border-radius:10px;font:inherit;color:inherit}" +
    ".rzf h2{font-size:1.05rem;margin:0 0 .75rem}.rzf label{display:block;margin:.6rem 0 .25rem;font-size:.9rem}" +
    ".rzf select,.rzf textarea,.rzf input[type=email]{width:100%;box-sizing:border-box;padding:.55rem .65rem;border:1px solid rgba(127,127,127,.5);border-radius:6px;font:inherit;color:inherit;background:transparent}" +
    ".rzf select option{color:#111}.rzf textarea{min-height:7rem;resize:vertical}.rzf button{margin-top:.85rem;padding:.55rem 1.1rem;border:0;border-radius:6px;font:inherit;cursor:pointer;background:#1f2937;color:#fff}" +
    ".rzf button[disabled]{opacity:.6}.rzf .rzf-note{font-size:.8rem;opacity:.75;margin:.75rem 0 0}" +
    ".rzf .rzf-msg{margin:.75rem 0 0;font-weight:600}.rzf .rzf-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}";

  function build(cfg) {
    var lang = (me.getAttribute("data-lang") || document.documentElement.lang || cfg.language || "en").slice(0, 2).toLowerCase();
    var t = T[lang] || T.en;
    var host = document.getElementById("redazia-feedback");
    if (!host) { host = el("div", { id: "redazia-feedback" }); me.parentNode.insertBefore(host, me.nextSibling); }
    var style = el("style"); style.textContent = CSS; host.appendChild(style);
    var form = el("form", { "class": "rzf", novalidate: "" });
    var id = "rzf" + Math.random().toString(36).slice(2, 8);
    form.appendChild(el("h2", null, t.title));
    form.appendChild(el("label", { "for": id + "k" }, t.kind));
    var kind = el("select", { id: id + "k", name: "kind" });
    ["error", "comment", "other"].forEach(function (k) { kind.appendChild(el("option", { value: k }, t[k])); });
    form.appendChild(kind);
    form.appendChild(el("label", { "for": id + "m" }, t.message));
    var msg = el("textarea", { id: id + "m", name: "message", maxlength: "2000", required: "" });
    form.appendChild(msg);
    form.appendChild(el("label", { "for": id + "e" }, t.email));
    var email = el("input", { id: id + "e", name: "email", type: "email", autocomplete: "email", maxlength: "254", required: "" });
    form.appendChild(email);
    var hp = el("div", { "class": "rzf-hp", "aria-hidden": "true" });
    var trap = el("input", { name: "website", tabindex: "-1", autocomplete: "off" });
    hp.appendChild(trap); form.appendChild(hp);
    var captcha = el("div"); form.appendChild(captcha);
    var btn = el("button", { type: "submit" }, t.send); form.appendChild(btn);
    var out = el("p", { "class": "rzf-msg", role: "status", "aria-live": "polite" }); form.appendChild(out);
    var note = el("p", { "class": "rzf-note" }, t.note + " ");
    var privacy = me.getAttribute("data-privacy");
    if (privacy) note.appendChild(el("a", { href: privacy }, t.privacy));
    form.appendChild(note);
    host.appendChild(form);

    var widget = null;
    if (cfg.sitekey) {
      var s = el("script", { src: "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit", async: "" });
      s.onload = function () { widget = window.turnstile.render(captcha, { sitekey: cfg.sitekey, language: lang }); };
      document.head.appendChild(s);
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      out.textContent = "";
      if (!/^\S+@\S+\.\S+$/.test(email.value.trim())) { out.textContent = t.e_email; return; }
      var text = msg.value.trim();
      if (text.length < 10 || text.length > 2000) { out.textContent = t.e_length; return; }
      btn.disabled = true;
      var body = { kind: kind.value, message: text, email: email.value.trim(), website: trap.value, lang: lang,
        url: location.href.split("#")[0], title: document.title,
        turnstile: widget !== null && window.turnstile ? window.turnstile.getResponse(widget) : null };
      // As text: no preflight request; the server reads the JSON all the same.
      fetch(api, { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(body), credentials: "omit" })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (r) {
          if (r.ok) { form.replaceChildren(el("p", { "class": "rzf-msg", role: "status" }, t.sent)); return; }
          out.textContent = t["e_" + r.error] || t.e_other;
          btn.disabled = false;
          if (widget !== null && window.turnstile) window.turnstile.reset(widget);
        })
        .catch(function () { out.textContent = t.e_other; btn.disabled = false; });
    });
  }

  fetch(api, { credentials: "omit" })
    .then(function (r) { return r.json(); })
    .then(function (cfg) { if (cfg && cfg.enabled) build(cfg); })
    .catch(function () {});
})();
