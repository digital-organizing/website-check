#!/usr/bin/env python3
"""Share buttons (WhatsApp, Telegram, Facebook, LinkedIn, X, Threema, E-Mail …): what really gets shared.

Usage: python share_links.py WORKDIR   → WORKDIR/share.json

A plain link check says almost nothing about share links: sharer URLs answer bots with 400 or a login
page, and a broken Telegram link silently redirects to telegram.org. So each share link is decoded and
checked for what matters:
  - platform format: required parameters present and not empty (Telegram needs url=, Facebook u=, …)
  - pre-filled text / e-mail subject in the language of the page
  - the shared URL (url= parameter or a link inside the text) opens in a real browser – including
    JavaScript redirects of short links – and lands on a page in the right language
  - the link preview: Facebook, LinkedIn, WhatsApp, Telegram … do not run JavaScript, so their preview
    comes from the raw HTML of the shared URL (only HTTP redirects are followed). A short link that
    forwards via JavaScript therefore shows *its own* OG tags, not the ones of the campaign page.
"""
import glob
import io
import os
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs, urljoin, unquote

import requests

from common import UA, LangMap, load_json, save_json, foreign_strength, detect_lang, bare_host, launch_browser

# name, host regex, path regex, url params, text params, rule
#   rule "url"      → a non-empty url parameter is required
#   rule "text"     → a non-empty text parameter is required
#   rule "url|text" → at least one of both
PLATFORMS = [
    ("WhatsApp", r"(^|\.)wa\.me$", r".*", [], ["text"], "text"),
    ("WhatsApp", r"(api|web)\.whatsapp\.com$", r"^/send", [], ["text"], "text"),
    ("Telegram", r"(^|\.)(t|telegram)\.me$", r"^/share(/url)?/?$", ["url"], ["text"], "url"),
    ("Facebook", r"(^|\.)facebook\.com$", r"^/(sharer(/sharer)?\.php|share\.php|sharer/?)$", ["u"], ["quote"], "url"),
    ("Facebook", r"(^|\.)facebook\.com$", r"^/dialog/(share|feed)", ["href", "link"], ["quote"], "url"),
    ("LinkedIn", r"(^|\.)linkedin\.com$", r"^/(shareArticle|sharing/share-offsite)", ["url"], ["title", "summary"], "url"),
    ("LinkedIn", r"(^|\.)linkedin\.com$", r"^/feed/?$", [], ["text"], "text"),
    ("X/Twitter", r"(^|\.)(twitter|x)\.com$", r"^/(intent/(tweet|post)|share)", ["url"], ["text"], "url|text"),
    ("Threema", r"(^|\.)threema\.id$", r"^/compose", [], ["text"], "text"),
    ("Bluesky", r"(^|\.)bsky\.app$", r"^/intent/compose", [], ["text"], "text"),
    ("Reddit", r"(^|\.)reddit\.com$", r"^/submit", ["url"], ["title"], "url"),
    ("Xing", r"(^|\.)xing\.com$", r"^/(spi/shares/new|social/share/spi|app/user)", ["url"], [], "url"),
    ("Pinterest", r"(^|\.)pinterest\.[a-z.]+$", r"^/pin/create", ["url"], ["description"], "url"),
    ("Mastodon", r".*", r"^/share$", ["url"], ["text"], "url|text"),
]
SCHEMES = {"whatsapp": ("WhatsApp", ["text"], "text"), "tg": ("Telegram", ["text", "url"], "url|text"),
           "threema": ("Threema", ["text"], "text"), "fb-messenger": ("Messenger", ["link"], "url"),
           "sms": ("SMS", ["body"], "text")}
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'«»]+")
PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"


def classify(href, raw=""):
    """Return (platform, params, url_keys, text_keys, rule) for a share link, else None."""
    h = (raw or href or "").strip()
    low = h.lower()
    if low.startswith("mailto:"):
        q = parse_qs(h.split("?", 1)[1], keep_blank_values=True) if "?" in h else {}
        q = {k.lower(): v for k, v in q.items()}
        if "subject" in q or "body" in q:  # plain contact addresses are not share links
            return "E-Mail", q, [], ["subject", "body"], "text"
        return None
    scheme = low.split(":", 1)[0]
    if scheme in SCHEMES:
        name, keys, rule = SCHEMES[scheme]
        q = parse_qs(re.split(r"[?&]", h, 1)[1], keep_blank_values=True) if re.search(r"[?&]", h) else {}
        return name, q, [k for k in keys if k in ("url", "link")], [k for k in keys if k not in ("url", "link")], rule
    if not href.startswith("http"):
        return None
    p = urlsplit(href)
    host = (p.hostname or "").lower()
    for name, hrx, prx, ukeys, tkeys, rule in PLATFORMS:
        if re.search(hrx, host) and re.search(prx, p.path or "/"):
            if name == "Mastodon" and not ({"url", "text"} & set(parse_qs(p.query))):
                continue
            if name == "WhatsApp" and "wa.me" in host and not re.fullmatch(r"/\d*/?", p.path or "/"):
                continue
            if name == "LinkedIn" and ukeys == [] and "shareActive" not in p.query:
                continue
            return name, parse_qs(p.query, keep_blank_values=True), ukeys, tkeys, rule
    return None


def is_share(href, raw=""):
    try:
        return classify(href, raw) is not None
    except Exception:
        return False


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self._t, self.lang, self.refresh = {}, "", False, "", ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k and k not in self.meta:
                self.meta[k] = a.get("content", "")
            if a.get("http-equiv", "").lower() == "refresh":
                self.refresh = a.get("content", "")
        elif tag == "title":
            self._t = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._t = False

    def handle_data(self, data):
        if self._t:
            self.title += data


def raw_preview(url):
    """What a link-preview crawler sees: HTTP redirects only, no JavaScript."""
    out = {"url": url}
    try:
        r = requests.get(url, headers={"User-Agent": PREVIEW_UA, "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8"},
                         timeout=20, allow_redirects=True)
    except requests.exceptions.ProxyError:
        return {**out, "blocked": True}
    except requests.exceptions.RequestException as e:
        return {**out, "error": str(e)[:150]}
    if r.headers.get("x-deny-reason"):
        return {**out, "blocked": True}
    out.update(status=r.status_code, final=r.url)
    if "html" not in r.headers.get("content-type", ""):
        return out
    mp = MetaParser()
    try:
        mp.feed(r.text[:400_000])
    except Exception:
        pass
    m = {k: v for k, v in mp.meta.items() if k.startswith(("og:", "twitter:")) or k == "description"}
    js = re.search(r"(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)", r.text[:20000])
    out.update(meta=m, title=mp.title.strip(), lang=mp.lang, refresh=mp.refresh, js_redirect=js.group(1) if js else "")
    return out


def check_image(url, cache={}):
    if url in cache:
        return cache[url]
    res = {}
    try:
        r = requests.get(url, headers={"User-Agent": PREVIEW_UA}, timeout=20)
        if r.headers.get("x-deny-reason"):
            res = {"blocked": True}
        elif r.status_code >= 400:
            res = {"error": f"HTTP {r.status_code}"}
        else:
            from PIL import Image
            im = Image.open(io.BytesIO(r.content))
            res = {"w": im.size[0], "h": im.size[1], "bytes": len(r.content)}
    except requests.exceptions.ProxyError:
        res = {"blocked": True}
    except Exception as e:
        res = {"error": str(e)[:100]}
    cache[url] = res
    return res


ODD_CASE = re.compile(r"\b[a-zäöüéèàç]+[A-ZÄÖÜÉÈÀ]{1,2}[a-zäöüéèàç]+\b")  # «wIll», «deR» – not «iPhone»-style brands


FR_ELISION = re.compile(r"\b(la|le|de|que|je|ne|me|te|se)\s+[aeiouyàâéèêîôûh]\w*", re.I)  # «la explosion» → «l’explosion»
ADDRESS = {"fr": (re.compile(r"\b(tu|toi|ton|ta|tes)\b|\bt[’']", re.I), re.compile(r"\b(vous|votre|vos)\b|^\s*\w+ez\b", re.I | re.M)),
           "de": (re.compile(r"\b(du|dich|dir|dein\w*)\b", re.I), re.compile(r"\b(Ihnen|Ihre[mnrs]?)\b|(?<=[a-zäöü,] )Sie\b|^\s*\w+en Sie\b", re.M))}


def address_mix(texts, L):
    """«Signez …» (vous) in the subject but «Tu te joins …» (tu) in the body."""
    if L not in ADDRESS:
        return None
    informal, formal = ADDRESS[L]
    t = "\n".join(texts)
    if informal.search(t) and formal.search(t):
        return f"Du- und Sie-Form gemischt ({'tu/vous' if L == 'fr' else 'du/Sie'})"
    return None


def text_problems(text, L, langs):
    """Language and typo hints for a user-visible text in language L."""
    probs = []
    for X in (x for x in langs if x != L):
        if foreign_strength(text, X, L) == "strong":
            probs.append(("mittel", f"nicht übersetzt ({X.upper()})"))
            break
    if L == "fr":
        el = [m.group(0) for m in FR_ELISION.finditer(text) if not re.match(r"\w+\s+h", m.group(0), re.I)]
        if el:
            probs.append(("mittel", "Elision fehlt: " + ", ".join(f"«{x}»" for x in el[:3])))
    odd = [w for w in ODD_CASE.findall(text) if not re.match(r"^(i|e|mc|mac)[A-Z]", w)]
    if odd:
        probs.append(("hinweis", "auffällige Gross-/Kleinschreibung: " + ", ".join(sorted(set(odd))[:5])))
    return probs


def main(wd):
    cfg = load_json(os.path.join(wd, "config.json"))
    lm = LangMap(cfg)
    langs = lm.codes
    pages = {p["url"]: p for p in (load_json(f) for f in glob.glob(os.path.join(wd, "pages", "*.json")))}
    pages = {u: p for u, p in pages.items() if p.get("status") and p["status"] < 400 and p.get("final_url", u) == u}

    links = {}  # href -> record
    for u, p in sorted(pages.items()):
        L = p.get("lang_cfg")
        for ln in p.get("links", []):
            c = classify(ln["href"], ln.get("raw") or "")
            if not c:
                continue
            name, q, ukeys, tkeys, rule = c
            key = ln.get("raw") if name == "E-Mail" else ln["href"]
            rec = links.setdefault(key, {"href": key, "platform": name, "label": ln["text"], "lang": L, "pages": [],
                                         "params": {k: v[0] for k, v in q.items()}, "problems": [], "shared": []})
            if u not in rec["pages"]:
                rec["pages"].append(u)
            if rec["problems"] or rec.get("_done"):
                continue
            rec["_done"] = True
            P = rec["params"]
            url_vals = [P.get(k, "") for k in ukeys]
            text_vals = [P.get(k, "") for k in tkeys if P.get(k)]
            has_url = any(v.strip() for v in url_vals)
            has_text = any(v.strip() for v in text_vals)
            if rule == "url" and not has_url:
                present = [k for k in ukeys if k in P]
                what = f"Parameter «{present[0]}=» ist leer" if present else f"Parameter «{ukeys[0]}=» fehlt"
                extra = " – Telegram leitet dann ohne Teilen-Dialog auf telegram.org weiter" if name == "Telegram" else ""
                hint = " Die URL steht nur im Text; sie gehört in den url-Parameter." if URL_IN_TEXT.search(" ".join(text_vals)) else ""
                rec["problems"].append(("kritisch", f"{name}-Teilen-Link funktioniert nicht", f"{what}{extra}.{hint}"))
            elif rule == "text" and not has_text:
                rec["problems"].append(("kritisch", f"{name}-Teilen-Link ohne Text", f"Parameter «{tkeys[0]}=» fehlt oder ist leer."))
            elif rule == "url|text" and not (has_url or has_text):
                rec["problems"].append(("kritisch", f"{name}-Teilen-Link ohne Inhalt", "Weder url= noch text= gesetzt."))
            known = set(ukeys) | set(tkeys) | {"mini", "source", "hashtags", "via", "related", "app_id", "display",
                                              "redirect_uri", "phone", "title", "cc", "bcc", "shareactive", "media"}
            odd_keys = [k for k in P if k.lower() not in known and not k.lower().startswith("utm_")]
            if odd_keys and text_vals:
                rec["problems"].append(("mittel", f"{name}-Teilen-Text vermutlich abgeschnitten",
                                        f"Unerwartete Parameter {', '.join(odd_keys[:3])} – meist ein nicht kodiertes «&» oder «#» im Text."))
            for k in tkeys:
                v = P.get(k, "")
                if not v or not L:
                    continue
                for sev, t in text_problems(URL_IN_TEXT.sub("", v), L, langs):
                    rec["problems"].append((sev, f"{name}-Teilen-Text ({k}): {t}", v[:200]))
            mix = address_mix([P.get(k, "") for k in tkeys], L)
            if mix:
                rec["problems"].append(("hinweis", f"{name}-Teilen-Text: {mix}", " | ".join(P.get(k, "")[:120] for k in tkeys if P.get(k))))
            shared = [v.strip() for v in url_vals if v.strip().startswith("http")]
            for v in text_vals:
                shared += [m.rstrip(".,;:!?)»") for m in URL_IN_TEXT.findall(v)]
            rec["shared"] = list(dict.fromkeys(shared))

    # --------------------------------------------- shared targets: browser + raw preview
    targets = {}
    for rec in links.values():
        for s in rec["shared"]:
            targets.setdefault(s, {"url": s, "platforms": set(), "langs": set(), "links": []})
            targets[s]["platforms"].add(rec["platform"])
            targets[s]["langs"].add(rec["lang"])
            targets[s]["links"].append(rec["href"])
    if targets:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = launch_browser(pw)
            ctx = b.new_context(user_agent=UA, locale="de-CH")
            pg = ctx.new_page()
            for t in targets.values():
                try:
                    r = pg.goto(t["url"], wait_until="domcontentloaded", timeout=45000)
                    try:
                        pg.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    pg.wait_for_timeout(1500)
                    t["final"] = pg.url
                    t["status"] = r.status if r else None
                    t["html_lang"] = pg.evaluate("document.documentElement.lang || ''")
                    t["title"] = pg.title()
                except Exception as e:
                    t["error"] = str(e).split("\n")[0][:160]
            b.close()
    for t in targets.values():
        t["platforms"], t["langs"] = sorted(t["platforms"]), sorted(x for x in t["langs"] if x)
        t["problems"] = []
        exp = t["langs"][0] if len(t["langs"]) == 1 else None
        if t.get("error") or (t.get("status") or 0) >= 400:
            if "ERR_TUNNEL" in t.get("error", "") or "ERR_PROXY" in t.get("error", ""):
                t["blocked"] = True
                continue
            t["problems"].append(("kritisch", "Geteilter Link funktioniert nicht", t.get("error") or f"HTTP {t['status']}"))
            continue
        fl = lm.lang_of(t.get("final", "")) or (t.get("html_lang") or "").split("-")[0].lower() or None
        t["final_lang"] = fl
        if exp and fl and fl != exp:
            t["problems"].append(("mittel", f"Geteilter Link führt auf {fl.upper()}-Seite statt {exp.upper()}", f"{t['url']} → {t['final']}"))
        fq = parse_qs(urlsplit(t.get("final", "")).query)
        t["tracking"] = ", ".join(f"{k}={v[0]}" for k, v in fq.items() if k.startswith("utm_") or k in ("channel", "source", "ref", "mtm_campaign"))
        # previews are shown by messengers, social networks and many mail clients alike
        pv = raw_preview(t["url"])
        t["preview"] = pv
        if pv.get("blocked"):
            continue
        if pv.get("error") or (pv.get("status") or 0) >= 400:
            t["problems"].append(("kritisch", "Link-Vorschau: Seite für Vorschau-Crawler nicht erreichbar",
                                  pv.get("error") or f"HTTP {pv['status']}"))
            continue
        m = pv.get("meta", {})
        via = ""
        if pv.get("js_redirect") or pv.get("refresh"):
            via = f" (Vorschau stammt von der Zwischenseite {urlsplit(pv.get('final') or t['url']).netloc}, die per " \
                  f"{'JavaScript' if pv.get('js_redirect') else 'Meta-Refresh'} weiterleitet)"
            t["problems"].append(("hinweis", "Link-Vorschau stammt von einer Weiterleitungsseite",
                                  f"{t['url']} leitet erst im Browser weiter; Facebook/LinkedIn/WhatsApp zeigen die OG-Tags dieser Zwischenseite."))
        prev_title = m.get("og:title") or ""
        if not prev_title:
            t["problems"].append(("mittel", "Link-Vorschau: og:title fehlt oder ist leer",
                                  f"Plattformen weichen auf <title> «{pv.get('title', '')[:80]}» aus{via}."))
        if not m.get("og:description"):
            t["problems"].append(("mittel", "Link-Vorschau: og:description fehlt oder ist leer", via.strip(" ()")))
        img = m.get("og:image", "")
        if not img:
            t["problems"].append(("mittel", "Link-Vorschau: og:image fehlt", via.strip(" ()")))
        else:
            if not img.startswith("http"):
                t["problems"].append(("mittel", "Link-Vorschau: og:image ist keine absolute URL",
                                      f"«{img}» – Facebook und LinkedIn verlangen eine vollständige https-URL{via}."))
                img = urljoin(pv.get("final") or t["url"], img)
            ir = check_image(img)
            t["preview_image"] = {**ir, "url": img}
            if ir.get("error"):
                t["problems"].append(("kritisch", "Link-Vorschau: og:image lädt nicht", f"{img} ({ir['error']})"))
            elif ir.get("w") and (ir["w"] < 1200 or ir["h"] < 630):
                t["problems"].append(("hinweis", f"Link-Vorschau: og:image {ir['w']}×{ir['h']} (empfohlen 1200×630)", img))
        for k in ("og:url", "og:type"):
            if k in m and not m[k]:
                t["problems"].append(("hinweis", f"Link-Vorschau: {k} ist leer", via.strip(" ()")))
        if exp:
            for k in ("og:title", "og:description"):
                if m.get(k):
                    for sev, txt in text_problems(m[k], exp, langs):
                        t["problems"].append((sev, f"Link-Vorschau {k}: {txt}", m[k][:200]))
        pl = (pv.get("lang") or "").split("-")[0].lower()
        if exp and pl and pl != exp and via:
            t["problems"].append(("hinweis", f"Weiterleitungsseite hat <html lang=\"{pv['lang']}\">", t["url"]))

    # --------------------------------------------- consistency between languages
    per_lang = defaultdict(set)
    for rec in links.values():
        if rec["lang"]:
            per_lang[rec["lang"]].add(rec["platform"])
    consistency = []
    if len(per_lang) > 1:
        allp = set().union(*per_lang.values())
        for L, ps in per_lang.items():
            if allp - ps:
                consistency.append((L, sorted(allp - ps)))

    for rec in links.values():
        rec.pop("_done", None)
    out = {"links": sorted(links.values(), key=lambda r: (r["lang"] or "", r["platform"])),
           "targets": sorted(targets.values(), key=lambda t: t["url"]), "missing_platforms": consistency}
    save_json(os.path.join(wd, "share.json"), out)
    n_prob = sum(len(r["problems"]) for r in links.values()) + sum(len(t["problems"]) for t in targets.values())
    print(f"{len(links)} Teilen-Links, {len(targets)} geteilte Ziel-URLs, {n_prob} Probleme")
    for r in out["links"]:
        for sev, title, _ in r["problems"]:
            print(f"  {sev:8} {r['lang'] or '-':3} {title}")
    for t in out["targets"]:
        for sev, title, _ in t["problems"]:
            print(f"  {sev:8} {'/'.join(t['langs']):3} {title}  [{t['url']}]")


if __name__ == "__main__":
    main(sys.argv[1])
