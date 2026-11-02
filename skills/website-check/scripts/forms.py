#!/usr/bin/env python3
"""Form inventory and real test submissions.

  python forms.py WORKDIR inventory      → WORKDIR/forms_inventory.json (deduplicated per language)
  python forms.py WORKDIR submit [--only ID,ID]  → WORKDIR/forms_results.json + screenshots

Every entry in the inventory has "submit": true/false. Login and payment forms default to
false (never enter payment data). Review/confirm the inventory with the user before `submit`,
because submissions create real entries (newsletter lists, CRM, e-mails to the client).
"""
import argparse
import datetime
import glob
import hashlib
import os
import re
from urllib.parse import urlsplit

import requests
from playwright.sync_api import sync_playwright

from common import UA, LangMap, load_json, save_json, bare_host, normalize, launch_browser

PROVIDERS = r"tally\.so|hsforms|hubspot|typeform|docs\.google\.com/forms|mailchimp|list-manage|cleverreach|jotform|formstack|raisenow|payrexx|fundraisingbox|twingle|mailerlite|brevo|sendinblue|getresponse|zohopublic|webling|civicrm|campax|act\.|actionnetwork|formbricks|forms\.office"
PAYMENT = re.compile(r"card|karte|kreditkarte|cvc|cvv|iban|expiry|ablauf|betrag|amount|montant|importo|twint|payrexx|raisenow|stripe|datatrans|postfinance|spende|don\b|donation", re.I)
SUCCESS = re.compile(r"danke|vielen dank|erfolgreich|wurde gesendet|gesendet|abgeschickt|bestätig|eingetragen|angemeldet|"
                     r"merci|envoy|enregistr|confirm|inscri|grazie|inviat|registrat|thank|success|received", re.I)
ERRORTXT = re.compile(r"fehler|ungültig|erforderlich|pflichtfeld|bitte (geben|füllen|wählen)|erreur|invalide|obligatoire|"
                      r"veuillez|errore|obbligatorio|error|invalid|required", re.I)


def classify(form):
    names = " ".join((f["name"] + " " + f["label"] + " " + f["placeholder"] + " " + f["type"]) for f in form["fields"]).lower()
    types = {f["type"] for f in form["fields"]}
    if "password" in types:
        return "Login"
    if PAYMENT.search(names):
        return "Zahlung/Spende"
    if "search" in types or re.search(r"\b(q|s|search|suche|recherche)\b", " ".join(f["name"] for f in form["fields"]).lower()):
        return "Suche"
    if "textarea" in types or "textarea" in {f["tag"] for f in form["fields"]}:
        return "Kontakt"
    emailish = [f for f in form["fields"] if f["type"] == "email" or "mail" in f["name"].lower()]
    inputs = [f for f in form["fields"] if f["type"] not in ("checkbox", "radio", "submit", "button")]
    if emailish and (len(inputs) == 1 or "newsletter" in (form.get("cls", "") + " ".join(form.get("buttons", []))).lower()):
        return "Newsletter"
    return "Anmeldung/Petition" if emailish else "Sonstiges"


def inventory(wd):
    cfg = load_json(os.path.join(wd, "config.json"))
    lm = LangMap(cfg)
    items = {}
    for f in glob.glob(os.path.join(wd, "pages", "*.json")):
        p = load_json(f)
        if p.get("final_url") and p["final_url"] != p["url"]:
            continue  # redirected URL – the target page is inventoried on its own
        lang = p.get("lang_cfg")
        for form in p.get("forms", []):
            if not form["fields"]:
                continue
            act = urlsplit(form["action"])
            # many CMS post to the current page → treat "self" actions as identical across pages
            target = "self" if (not form["action"] or act.path.rstrip("/") == urlsplit(p["url"]).path.rstrip("/")) \
                else act.netloc + act.path
            sig = "|".join(sorted(x["name"] or x["type"] for x in form["fields"])) + "@" + target
            key = hashlib.sha1(f"{lang}:{sig}".encode()).hexdigest()[:10]
            it = items.setdefault(key, {"id": key, "lang": lang, "kind": "html", "type": classify(form),
                                        "fields": form["fields"], "buttons": form["buttons"], "captcha": form["captcha"],
                                        "action": form["action"], "method": form["method"], "pages": [], "form_index": form["index"]})
            it["pages"].append(p["url"])
        for src in p.get("iframes", []):
            if src and re.search(PROVIDERS, src):
                key = hashlib.sha1(f"{lang}:iframe:{src.split('?')[0]}".encode()).hexdigest()[:10]
                it = items.setdefault(key, {"id": key, "lang": lang, "kind": "iframe", "type": "Eingebettetes Formular",
                                            "frame_src": src, "fields": [], "buttons": [], "captcha": False, "pages": []})
                it["pages"].append(p["url"])
    extra = []
    for it in items.values():
        if it["kind"] == "iframe":
            it["redirects"] = embedded_redirects(it["frame_src"], lm)
            extra += [(u, f"Im eingebetteten Formular hinterlegt ({it['frame_src'].split('?')[0]})") for u in it["redirects"]]
    for it in items.values():
        it["pages"] = sorted(set(it["pages"]), key=len)
        it["test_page"] = it["pages"][0]
        it["submit"] = it["type"] not in ("Login", "Zahlung/Spende")
        if it["type"] == "Zahlung/Spende":
            it["note"] = "Zahlungs-/Spendenformular: wird nicht abgeschickt, nur Felder/Labels geprüft."
    out = sorted(items.values(), key=lambda x: (x["lang"] or "", x["type"], x["test_page"]))
    save_json(os.path.join(wd, "forms_inventory.json"), out)
    print(f"{len(out)} verschiedene Formulare gefunden:")
    for it in out:
        print(f"  [{it['id']}] {it['lang']} {it['type']:22} {'abschicken' if it['submit'] else 'NICHT abschicken':17} "
              f"{it['test_page']}  (auf {len(it['pages'])} Seiten){'  CAPTCHA' if it['captcha'] else ''}")
        for u in it.get("redirects", []):
            print(f"      leitet evtl. weiter auf {u}")
    queue_urls(wd, extra, "In eingebetteten Formularen hinterlegte Seiten (z.B. Danke-Seiten)")


def value_for(meta, cfg):
    t = cfg["form_test"]
    s = " ".join([meta["name"], meta["id"], meta["label"], meta["placeholder"], meta["autocomplete"]]).lower()
    typ = meta["type"]
    if typ == "email" or "mail" in s:
        return t["email"]
    if typ == "tel" or re.search(r"tel|phone|handy|mobile|natel", s):
        return "+41 44 000 00 00"
    if typ == "url" or re.search(r"website|webseite|url", s):
        return "https://digitalorganizing.ch"
    if typ == "number":
        return meta.get("min") or "1"
    if typ == "date":
        return (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    if re.search(r"vorname|first|prénom|prenom|nome\b|given", s):
        return t["first_name"]
    if re.search(r"nachname|last|family|cognome|surname|nom de famille|\bnom\b", s):
        return t["last_name"]
    if re.search(r"\bname\b|fullname|full_name|your-name", s):
        return f"{t['first_name']} {t['last_name']}"
    if re.search(r"plz|zip|postal|postcode|npa|\bcap\b", s):
        return "8000"
    if re.search(r"\bort\b|city|ville|localit|wohnort|town", s):
        return "Zürich"
    if re.search(r"stras|street|adress|rue|\bvia\b", s):
        return "Teststrasse 1"
    if re.search(r"firma|organisation|company|entreprise|azienda", s):
        return "digital/organizing"
    if meta["tag"] == "textarea" or re.search(r"nachricht|message|kommentar|comment|bemerkung|anliegen", s):
        return t["message"]
    return "Test"


FIELDS_JS = r"""(form) => [...form.querySelectorAll('input, select, textarea')].map((el, i) => {
  let label = '';
  if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) label = l.textContent.trim(); }
  if (!label && el.closest('label')) label = el.closest('label').textContent.trim();
  const r = el.getBoundingClientRect();
  return {i, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(), name: el.name || '', id: el.id || '',
          label: label.slice(0, 120), placeholder: el.placeholder || '', autocomplete: el.autocomplete || '',
          required: el.required || el.getAttribute('aria-required') === 'true', min: el.min || '',
          visible: r.width > 0 && r.height > 0, checked: !!el.checked};
})"""


def fill_and_submit(root, form, cfg, log):
    fields = form.evaluate(FIELDS_JS)
    loc = form.locator("input, select, textarea")
    radios_done, filled = set(), []
    for f in fields:
        el = loc.nth(f["i"])
        try:
            if f["type"] in ("hidden", "submit", "button", "reset", "image") or not f["visible"]:
                continue
            if f["type"] == "file":
                filled.append(f"{f['name']}: (Datei-Upload übersprungen)")
                continue
            if f["type"] == "checkbox":
                consent = re.search(r"datenschutz|privacy|einverstanden|akzept|accept|consent|agb|conditions|zustimm|j'accepte|accetto|newsletter",
                                    (f["label"] + f["name"]).lower())
                if f["required"] or consent:
                    el.check(timeout=3000, force=True)
                    filled.append(f"☑ {f['label'][:40] or f['name']}")
                continue
            if f["type"] == "radio":
                if f["name"] not in radios_done:
                    el.check(timeout=3000, force=True)
                    radios_done.add(f["name"])
                continue
            if f["tag"] == "select":
                opts = el.evaluate("s => [...s.options].filter(o => o.value && !o.disabled).map(o => o.value)")
                if opts:
                    el.select_option(opts[0])
                    filled.append(f"{f['name']}={opts[0]}")
                continue
            v = value_for(f, cfg)
            el.fill(v, timeout=3000)
            filled.append(f"{f['label'][:30] or f['name'] or f['placeholder']}={v[:30]}")
        except Exception as e:
            log(f"    Feld {f['name']}: {str(e)[:80]}")
    btn = form.locator("button[type=submit], input[type=submit], button:not([type])").first
    if not btn.count():
        btn = root.locator("button[type=submit], input[type=submit]").first
    btn.click(timeout=5000)
    return filled


def submit_all(wd, only):
    cfg = load_json(os.path.join(wd, "config.json"))
    inv = load_json(os.path.join(wd, "forms_inventory.json"), [])
    results = load_json(os.path.join(wd, "forms_results.json"), {})
    os.makedirs(os.path.join(wd, "screens"), exist_ok=True)
    with sync_playwright() as pw:
        b = launch_browser(pw)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        for it in inv:
            if not it["submit"] or (only and it["id"] not in only):
                continue
            log = lambda m: print(m, flush=True)
            log(f"[{it['id']}] {it['type']} auf {it['test_page']}")
            page = ctx.new_page()
            posts, console = [], []
            page.on("console", lambda m: console.append(m.text[:200]) if m.type == "error" else None)
            page.on("response", lambda r: posts.append({"url": r.url[:200], "status": r.status, "method": r.request.method})
                    if r.request.method in ("POST", "PUT") else None)
            res = {"id": it["id"], "page": it["test_page"], "lang": it["lang"], "type": it["type"]}
            try:
                page.goto(it["test_page"], wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                from viewports import COOKIE_JS
                page.evaluate(COOKIE_JS)
                page.wait_for_timeout(500)
                root = page
                if it["kind"] == "iframe":
                    prefix = it["frame_src"].split("?")[0]
                    frames = [f for f in page.frames if f.url.startswith(prefix)]
                    if not frames:
                        raise RuntimeError("Eingebettetes Formular (iframe) lädt nicht")
                    root = frames[0]
                    form = root.locator("form").first
                else:
                    form = root.locator("form").nth(it["form_index"])
                form.scroll_into_view_if_needed(timeout=5000)
                before_text = page.evaluate("document.body.innerText")
                before_url = page.url
                console.clear()
                res["filled"] = fill_and_submit(root, form, cfg, log)
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                after_text = page.evaluate("document.body.innerText") if not page.is_closed() else ""
                new_lines = [l.strip() for l in after_text.split("\n") if l.strip() and l.strip() not in before_text][:15]
                try:
                    invalid = root.evaluate("() => document.querySelectorAll('form :invalid, [aria-invalid=true]').length")
                except Exception:
                    invalid = 0  # frame/page gone: the form navigated away (e.g. Tally redirecting to a thank-you page)
                res.update(url_changed=page.url != before_url, final_url=page.url, new_text=new_lines,
                           invalid_fields=invalid, posts=posts[-5:], console_errors=console[:5])
                ok_post = any(200 <= p["status"] < 400 for p in posts)
                bad_post = [p for p in posts if p["status"] >= 400]
                success_txt = [l for l in new_lines if SUCCESS.search(l)]
                error_txt = [l for l in new_lines if ERRORTXT.search(l)]
                if bad_post:
                    res["result"], res["detail"] = "fehlgeschlagen", f"Server antwortet HTTP {bad_post[-1]['status']}"
                elif success_txt or (res["url_changed"] and ok_post):
                    res["result"], res["detail"] = "erfolgreich", (success_txt or [res["final_url"]])[0][:200]
                elif error_txt or invalid:
                    res["result"] = "fehlgeschlagen"
                    res["detail"] = "Validierungsfehler: " + ("; ".join(error_txt[:3]) or f"{invalid} ungültige Felder")
                elif it["captcha"]:
                    res["result"], res["detail"] = "unklar", "Captcha vorhanden – keine Bestätigung sichtbar, manuell prüfen"
                elif ok_post:
                    res["result"], res["detail"] = "unklar", "Daten gesendet (HTTP OK), aber keine sichtbare Bestätigung"
                else:
                    res["result"], res["detail"] = "fehlgeschlagen", "Keine Reaktion auf Absenden (kein Request, keine Meldung)"
                res["success_text"] = success_txt[:3]
                shot = os.path.join(wd, "screens", f"form_{it['id']}.jpg")
                page.screenshot(path=shot, type="jpeg", quality=50)
                res["screenshot"] = os.path.relpath(shot, wd)
            except Exception as e:
                res["result"], res["detail"] = "fehlgeschlagen", "Test nicht durchführbar: " + str(e).split("\n")[0][:200]
            page.close()
            log(f"    → {res['result']}: {res.get('detail', '')}")
            results[it["id"]] = res
            save_json(os.path.join(wd, "forms_results.json"), results)
        b.close()
    queue_thank_you_pages(wd, results)


def queue_urls(wd, urls_via, headline):
    """Add pages the crawler cannot reach by links (thank-you pages, often with share buttons) to the crawl
    queue, so crawl.py / check_links.py / viewports.py / share_links.py cover them too."""
    st_path = os.path.join(wd, "crawl_state.json")
    st = load_json(st_path)
    if not st:
        return
    new = []
    for u, via in urls_via:
        n = normalize(u)
        if n not in st["seen"]:
            st["queue"].append([n, 1, via])
            st["seen"].append(n)
            st["finished"] = False
            new.append(n)
    if new:
        save_json(st_path, st)
        print(f"\n{headline} – in die Crawl-Queue gelegt:")
        for n in new:
            print("  +", n)
        print("→ crawl.py, check_links.py, viewports.py und share_links.py erneut laufen lassen.")


def queue_thank_you_pages(wd, results):
    lm = LangMap(load_json(os.path.join(wd, "config.json")))
    queue_urls(wd, [(r["final_url"], f"Nach Absenden von {r['page']}") for r in results.values()
                    if r.get("url_changed") and r.get("final_url") and lm.is_internal(r["final_url"])],
               "Neue Seiten nach dem Absenden (z.B. Danke-Seiten)")


def embedded_redirects(src, lm):
    """Internal URLs named in an embedded form's configuration (Tally, HubSpot … store the redirect target
    there). Finds thank-you pages even when the form is not submitted."""
    try:
        r = requests.get(src, headers={"User-Agent": UA}, timeout=20)
        txt = r.text.replace("\\/", "/")
    except requests.exceptions.RequestException:
        return []
    found = []
    for u in re.findall(r"https?://[^\s\"'<>\\]+", txt):
        u = u.rstrip(".,;)")
        if lm.is_internal(u) and u not in found:
            found.append(u)
    return found[:20]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("mode", choices=["inventory", "submit"])
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    if a.mode == "inventory":
        inventory(a.workdir)
    else:
        submit_all(a.workdir, set(x for x in a.only.split(",") if x))
