#!/usr/bin/env python3
"""Turn raw crawl data into findings.

Usage: python analyze.py WORKDIR   → WORKDIR/findings.json
Inputs: config.json, pages/, crawl_state.json, links.json, viewports/, forms_inventory.json, forms_results.json, share.json
All texts are German because the report is German.
"""
import glob
import hashlib
import io
import os
import re
import sys
from collections import defaultdict
from urllib.parse import urlsplit

import requests

from common import (UA, LangMap, load_json, save_json, normalize, bare_host, detect_lang,
                    foreign_strength, label_lang)
from preflight import probe

LANG_NAME = {"de": "DE", "fr": "FR", "it": "IT", "en": "EN", "rm": "RM"}
IMPRESSUM = re.compile(r"impressum|imprint|mentions[-_ ]?l[ée]gales|colophon|note[-_ ]legali|legal[-_ ]notice", re.I)
PRIVACY = re.compile(r"datenschutz|privacy|confidentialit|protection[-_ ]des[-_ ]donn|informativa|data[-_ ]protection|dsgvo|politique[-_ ]de[-_ ]confid", re.I)
SEV_ORDER = {"kritisch": 0, "mittel": 1, "hinweis": 2}


class Findings:
    def __init__(self):
        self.items = {}

    def add(self, sev, cat, title, detail="", url=None, lang=None, pages=None, evidence=None, screenshot=None, merge=True):
        """Same (sev, cat, title, detail, lang) merges into one finding listing all affected pages."""
        key = (sev, cat, title, detail, lang) if merge else (sev, cat, title, detail, lang, url)
        fid = hashlib.sha1(repr(key).encode()).hexdigest()[:10]
        f = self.items.setdefault(fid, {"id": fid, "sev": sev, "cat": cat, "title": title, "detail": detail,
                                        "lang": lang, "pages": [], "evidence": [], "screenshots": []})
        for p in (pages or ([url] if url else [])):
            if p not in f["pages"]:
                f["pages"].append(p)
        for e in (evidence or []):
            if e not in f["evidence"] and len(f["evidence"]) < 40:
                f["evidence"].append(e)
        if screenshot and screenshot not in f["screenshots"] and len(f["screenshots"]) < 6:
            f["screenshots"].append(screenshot)
        return f

    def sorted(self):
        return sorted(self.items.values(), key=lambda f: (SEV_ORDER[f["sev"]], f["cat"], -len(f["pages"]), f["title"]))


def main(wd):
    cfg = load_json(os.path.join(wd, "config.json"))
    lm = LangMap(cfg)
    langs, src = lm.codes, lm.source
    multi = len(langs) > 1
    F = Findings()
    st = load_json(os.path.join(wd, "crawl_state.json"), {})
    links = load_json(os.path.join(wd, "links.json"), {})
    all_pages = {p["url"]: p for p in (load_json(f) for f in glob.glob(os.path.join(wd, "pages", "*.json")))}
    inlinks = st.get("inlinks", {})
    pages = {u: p for u, p in all_pages.items() if p.get("status") and p["status"] < 400
             and "html" in p.get("content_type", "") and p.get("final_url", u) == u and lm.is_internal(u)}
    by_lang = defaultdict(list)
    for u, p in pages.items():
        by_lang[p["lang_cfg"]].append(u)

    # ------------------------------------------------ sandbox / blocked hosts
    host_status = {}

    def blocked(host):
        if host not in host_status:
            host_status[host] = probe(host)[0] == "blockiert" if (os.environ.get("HTTPS_PROXY") or os.path.isdir("/mnt/user-data")) else False
        return host_status[host]

    res_hosts = {urlsplit(r["url"]).hostname for p in all_pages.values() for r in p.get("failed_resources", [])}
    blocked_res = sorted(h for h in res_hosts if h and not lm.is_internal("https://" + h + "/") and blocked(h))
    render_degraded = any(urlsplit(r["url"]).hostname in blocked_res and r.get("type") in ("stylesheet", "script")
                          for p in all_pages.values() for r in p.get("failed_resources", []))
    unverifiable = defaultdict(list)  # reason -> urls

    # ------------------------------------------------ 1. pages that fail
    for u, p in all_pages.items():
        if not lm.is_internal(u):
            continue
        src_pages = inlinks.get(u, [])[:20]
        if p.get("status") and p["status"] >= 400:
            F.add("kritisch", "Links", f"Seite nicht erreichbar (HTTP {p['status']})", u, url=u, lang=p.get("lang_cfg"),
                  pages=src_pages or [u], evidence=[f"Verlinkt von: {s}" for s in src_pages[:10]] or ["Nur via Sitemap gefunden"], merge=False)
        elif p.get("status") is None:
            F.add("kritisch", "Links", "Seite lädt nicht", f"{u} – {p.get('error', '')[:150]}", url=u, pages=src_pages or [u], merge=False)

    # ------------------------------------------------ 2. link targets
    for u, p in pages.items():
        for ln in p.get("links", []):
            raw, h = (ln.get("raw") or "").strip(), ln["href"]
            key = raw if raw.lower().startswith(("mailto:", "tel:")) else normalize(h) if h.startswith("http") else None
            r = links.get(key) if key else None
            label = ln["text"] or "(ohne Text)"
            if r:
                if r["result"] == "kaputt" and not (key in all_pages and lm.is_internal(key)):
                    F.add("kritisch", "Links", "Defekter Link", f"{key} ({r.get('detail', '')})", lang=p["lang_cfg"],
                          url=u, evidence=[f"Linktext «{label}» auf {u}"])
                elif r["result"] == "kaputt":
                    pass  # internal page errors are reported in section 1 with all sources
                elif r["result"] == "umleitung" and lm.is_internal(key):
                    F.add("hinweis", "Links", "Interner Link mit Weiterleitung", f"{key} {r.get('detail', '')}",
                          lang=p["lang_cfg"], url=u)
                elif r["result"] in ("blockiert", "botschutz", "fehler"):
                    unverifiable[{"blockiert": "In der Sandbox blockiert", "botschutz": "Bot-Schutz (z.B. LinkedIn/Instagram)",
                                  "fehler": "Timeout/Fehler"}[r["result"]]].append(key)
            # anchors: same page (#x) or other crawled page (/seite#x)
            frag = urlsplit(h).fragment if h.startswith("http") else ""
            tgt = u if raw.startswith("#") else normalize(h) if frag else None
            if frag and tgt in pages and not frag.startswith("!") and "/" not in frag and "=" not in frag:
                if frag not in set(pages[tgt].get("ids", [])):
                    raw = raw if raw.startswith("#") else h
                    F.add("mittel", "Links", "Sprungmarke existiert nicht", raw, lang=p["lang_cfg"], url=u,
                          evidence=[f"Linktext «{label}»"])
            # links into another language (not the language switcher)
            if multi and h.startswith("http") and lm.is_internal(h):
                tl = lm.lang_of(h)
                if tl and p["lang_cfg"] and tl != p["lang_cfg"] and not ln.get("langish") and not ln.get("hreflang") \
                        and not label_lang(ln["text"]):
                    F.add("mittel", "Sprachversionen", f"Link führt auf {LANG_NAME.get(tl, tl)}-Seite statt {LANG_NAME.get(p['lang_cfg'])}",
                          f"Ziel: {normalize(h)}", lang=p["lang_cfg"], url=u, evidence=[f"«{label}» ({ln['zone']})"])

    # ------------------------------------------------ 3. broken resources / console
    for u, p in pages.items():
        for r in p.get("failed_resources", []):
            h = urlsplit(r["url"]).hostname
            if h in blocked_res:
                continue
            if r.get("status", 0) >= 400 or r.get("error", "").startswith("net::ERR_NAME"):
                kind = {"image": "Bild", "script": "Skript", "stylesheet": "Stylesheet", "font": "Schrift", "media": "Video/Audio"}.get(r.get("type"), "Ressource")
                F.add("mittel", "Technik", f"{kind} lädt nicht", f"{r['url'][:160]} ({r.get('status') or r.get('error')})",
                      lang=p["lang_cfg"], url=u)
        if not render_degraded:
            for c in p.get("console_errors", [])[:5]:
                F.add("hinweis", "Technik", "JavaScript-Fehler in der Konsole", c[:200], lang=p["lang_cfg"], url=u)

    # ------------------------------------------------ 4. language groups (page × language matrix)
    parent = {u: u for u in pages}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def members(root):
        return [u for u in pages if find(u) == root]

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        la = {pages[m]["lang_cfg"] for m in members(ra)}
        lb = {pages[m]["lang_cfg"] for m in members(rb)}
        if la & lb:
            return  # would put two pages of the same language in one row
        parent[rb] = ra

    for u, p in pages.items():  # hreflang first (most reliable)
        for alt in p.get("alternates", []):
            t = normalize(alt["href"])
            if t in pages and t != u:
                union(u, t)
            elif t != u and lm.is_internal(t) and alt["hreflang"] != "x-default":
                st_t = all_pages.get(t, {}).get("status")
                F.add("mittel", "Sprachversionen", "hreflang zeigt auf ungültige Seite",
                      f"{alt['hreflang']} → {t} ({'HTTP ' + str(st_t) if st_t else 'Weiterleitung/nicht gecrawlt'})",
                      lang=p["lang_cfg"], url=u)
    for u, p in pages.items():  # language switcher
        for ln in p.get("links", []):
            h = normalize(ln["href"]) if ln["href"].startswith("http") else None
            if not h or h not in pages or h == u:
                continue
            tl = pages[h]["lang_cfg"]
            if tl == p["lang_cfg"] or not (ln.get("langish") or ln.get("hreflang") or label_lang(ln["text"])):
                continue
            if lm.strip_prefix(h) == "/" and lm.strip_prefix(u) != "/":
                F.add("hinweis", "Sprachversionen", f"Sprachumschalter ({LANG_NAME.get(tl, tl)}) führt zur Startseite statt zur entsprechenden Seite",
                      "", lang=p["lang_cfg"], url=u)
                continue
            union(u, h)
    paths = defaultdict(list)  # shared slugs (/de/kontakt ↔ /fr/kontakt)
    for u in pages:
        paths[(lm.strip_prefix(u))].append(u)
    for same in paths.values():
        for other in same[1:]:
            union(same[0], other)

    rows = defaultdict(dict)
    for u in pages:
        rows[find(u)][pages[u]["lang_cfg"]] = u
    groups = []
    for r in rows.values():
        anchor = r.get(src) or next(iter(r.values()))
        groups.append({"key": anchor, "path": lm.strip_prefix(anchor), "pages": {l: r.get(l) for l in langs},
                       "title": (pages[anchor].get("h1") or [pages[anchor].get("title", "")])[0][:80]})
    groups.sort(key=lambda g: (g["path"].count("/"), g["path"]))
    counterpart = {}
    for g in groups:
        for l, u in g["pages"].items():
            if u:
                counterpart[u] = g["pages"].get(src)
        if multi:
            missing = [l for l in langs if not g["pages"].get(l)]
            present = [u for u in g["pages"].values() if u]
            for l in missing:
                F.add("mittel", "Sprachversionen", f"Keine {LANG_NAME.get(l, l)}-Version gefunden",
                      g["key"], lang=l, pages=present, merge=False)

    # ------------------------------------------------ 5. translations
    others = {l: [x for x in langs if x != l] for l in langs}
    shared_texts = defaultdict(lambda: defaultdict(set))  # lang -> text -> pages (nav/footer)
    for u, p in pages.items():
        L = p["lang_cfg"]
        if not L:
            continue
        declared = (p.get("lang") or "").split("-")[0].lower()
        if declared and declared != L:
            F.add("mittel", "Sprachversionen", f"<html lang> ist «{p.get('lang')}» statt {L}", "", lang=L, url=u)
        elif not declared:
            F.add("hinweis", "Sprachversionen", "<html lang> fehlt", "", lang=L, url=u)
        main_text = " ".join(b["t"] for b in p.get("blocks", []) if b["zone"] == "main")
        whole, conf = detect_lang(main_text, candidates=tuple(set(langs) | {"de", "fr", "it", "en"}))
        if whole and whole != L and conf > 0.6 and len(main_text) > 200:
            F.add("kritisch", "Übersetzung", f"Seite ist grösstenteils auf {LANG_NAME.get(whole, whole)} statt {LANG_NAME.get(L, L)}",
                  "", lang=L, url=u, merge=False)
            continue
        if L == src and not multi:
            continue
        src_blocks = set()
        cp = counterpart.get(u)
        if cp and cp != u and cp in pages:
            src_blocks = {b["t"] for b in pages[cp].get("blocks", [])} | {a["v"] for a in pages[cp].get("attrs", [])}
        strong, weak, same = [], [], []
        items = [(b["t"], b["zone"], "Text") for b in p.get("blocks", [])] + \
                [(a["v"], "main", a["a"]) for a in p.get("attrs", [])] + \
                [(p.get("title", ""), "main", "<title>"), (p.get("meta", {}).get("description", ""), "main", "meta description")]
        for text, zone, kind in items:
            if not text or label_lang(text) or len(text) < 2:
                continue
            hit = None
            for X in others[L]:
                s = foreign_strength(text, X, L)
                if s:
                    hit = (s, X)
                    break
            if not hit and L != src and text in src_blocks and len(text.split()) >= 3 and \
                    detect_lang(text)[0] not in (L,):
                hit = ("same", src)
            if not hit:
                continue
            line = f"{text[:160]}" + (f"  [{kind}]" if kind != "Text" else "")
            if zone in ("nav", "footer") and kind == "Text":
                shared_texts[L][(hit[0], line)].add(u)
            else:
                {"strong": strong, "weak": weak, "same": same}[hit[0]].append(line)
        if strong or same:
            F.add("mittel", "Übersetzung", "Nicht übersetzte Texte", "", lang=L, url=u,
                  evidence=strong + [f"{s}  (identisch mit {LANG_NAME.get(src)}-Version)" for s in same], merge=False)
        if weak:
            F.add("hinweis", "Übersetzung", "Möglicherweise nicht übersetzt (einzelne Wörter, evtl. Eigennamen)", "", lang=L,
                  url=u, evidence=weak, merge=False)
    for L, texts in shared_texts.items():
        for (strength, line), us in texts.items():
            sev = "hinweis" if strength == "weak" else "mittel"
            F.add(sev, "Übersetzung", "Nicht übersetzte Texte in Navigation/Footer (auf vielen Seiten)", "",
                  lang=L, pages=sorted(us), evidence=[line])

    # ------------------------------------------------ 6. OG tags
    img_cache = {}

    def check_image(url):
        if url in img_cache:
            return img_cache[url]
        res = {}
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
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
        img_cache[url] = res
        return res

    og_titles = defaultdict(list)
    og_table = []
    for u, p in sorted(pages.items()):
        L, m = p["lang_cfg"], p.get("meta", {})
        row = {"url": u, "lang": L, "problems": []}

        def prob(sev, title, detail=""):
            row["problems"].append(title)
            F.add(sev, "OG-Tags", title, "", lang=L, url=u, evidence=[f"{u}: {detail}"] if detail else None)

        for k in ("og:title", "og:description", "og:image", "og:url", "og:type"):
            row[k] = m.get(k, "")
            if not m.get(k):
                prob("mittel", f"{k} fehlt")
        row["og:locale"] = m.get("og:locale", "")
        if not m.get("og:locale"):
            prob("mittel" if multi else "hinweis", "og:locale fehlt")
        else:
            loc_l = m["og:locale"].replace("-", "_").split("_")[0].lower()
            if loc_l != L:
                prob("mittel", f"og:locale «{m['og:locale']}» passt nicht zur Sprache {LANG_NAME.get(L, L)}")
        if not m.get("twitter:card"):
            prob("hinweis", "twitter:card fehlt")
        if m.get("og:url"):
            ou = normalize(m["og:url"])
            canon = normalize(p.get("canonical") or u)
            if not m["og:url"].startswith("http"):
                prob("mittel", "og:url ist nicht absolut", m["og:url"])
            elif ou not in (normalize(u), canon):
                ol = lm.lang_of(ou)
                if multi and ol and ol != L:
                    prob("mittel", f"og:url zeigt auf {LANG_NAME.get(ol, ol)}-Seite", ou)
                else:
                    prob("mittel", "og:url zeigt auf eine andere Seite", ou)
        for k in ("og:title", "og:description"):
            v = m.get(k, "")
            if not v:
                continue
            for X in others[L]:
                if foreign_strength(v, X, L) == "strong":
                    prob("mittel", f"{k} ist nicht übersetzt ({LANG_NAME.get(X, X)})", v[:160])
                    break
            cp = counterpart.get(u)
            if f"{k} ist nicht übersetzt" in " ".join(row["problems"]):
                continue
            if multi and cp and cp != u and pages.get(cp, {}).get("meta", {}).get(k) == v and len(v.split()) >= 3:
                prob("mittel", f"{k} identisch mit {LANG_NAME.get(src)}-Version", v[:160])
        if m.get("og:title"):
            og_titles[(L, m["og:title"])].append(u)
        img = m.get("og:image", "")
        if img:
            if not img.startswith("http"):
                prob("mittel", "og:image ist keine absolute URL", img[:160])
            else:
                if img.startswith("http://"):
                    prob("mittel", "og:image wird über http:// statt https:// ausgeliefert", img[:160])
                r = check_image(img)
                row["img"] = r
                if r.get("blocked"):
                    unverifiable["In der Sandbox blockiert (OG-Bild)"].append(img)
                elif r.get("error"):
                    prob("kritisch", "og:image lädt nicht", f"{img[:140]} ({r['error']})")
                elif r.get("w"):
                    if r["w"] < 600 or r["h"] < 315:
                        prob("mittel", f"og:image zu klein ({r['w']}×{r['h']}, empfohlen 1200×630)", img[:140])
                    elif abs(r["w"] / r["h"] - 1.91) > 0.25:
                        prob("hinweis", f"og:image-Format {r['w']}×{r['h']} weicht von 1.91:1 ab (wird evtl. beschnitten)", img[:140])
                    if r["bytes"] > 5_000_000:
                        prob("hinweis", f"og:image sehr gross ({r['bytes'] // 1_000_000} MB)", img[:140])
        og_table.append(row)
    for (L, t), us in og_titles.items():
        if len(us) >= 3:
            F.add("hinweis", "OG-Tags", "Gleicher og:title auf mehreren Seiten (Standardwert?)", t[:120], lang=L, pages=us)

    # ------------------------------------------------ 7. legal pages
    legal = {}
    for kind, rx in (("Impressum", IMPRESSUM), ("Datenschutzerklärung", PRIVACY)):
        legal[kind] = {}
        for L in langs:
            cands = [u for u in by_lang[L] if rx.search(urlsplit(u).path) or rx.search(pages[u].get("title", ""))
                     or any(rx.search(h) for h in pages[u].get("h1", []))]
            # also: links whose text names the legal page, even if the target is on another language/host
            linked = defaultdict(int)
            for u in by_lang[L]:
                seen_here = set()
                for ln in pages[u].get("links", []):
                    if ln["href"].startswith("http") and (rx.search(ln["text"]) or rx.search(urlsplit(ln["href"]).path)):
                        seen_here.add(normalize(ln["href"]))
                for t in seen_here:
                    linked[t] += 1
            target = None
            if cands:
                target = max(cands, key=lambda c: linked.get(c, 0))
            elif linked:
                target = max(linked, key=linked.get)
            info = {"url": target, "linked_from": linked.get(target, 0) if target else 0, "pages_in_lang": len(by_lang[L])}
            legal[kind][L] = info
            if not target:
                F.add("kritisch", "Rechtliches", f"Kein(e) {kind} gefunden", "Weder Seite noch Link gefunden", lang=L,
                      pages=by_lang[L][:1], merge=False)
                continue
            tl = lm.lang_of(target)
            if target not in pages:
                st_t = all_pages.get(target, {}).get("status")
                if lm.is_internal(target) and st_t and st_t >= 400:
                    F.add("kritisch", "Rechtliches", f"{kind} nicht erreichbar (HTTP {st_t})", target, lang=L, url=target)
            if multi and tl and tl != L:
                F.add("mittel", "Rechtliches", f"{kind} verlinkt auf {LANG_NAME.get(tl, tl)}-Version", target, lang=L, url=target)
            n, total = info["linked_from"], info["pages_in_lang"]
            if total >= 3 and n < 0.9 * total:
                no_link = [u for u in by_lang[L] if target not in {normalize(x["href"]) for x in pages[u].get("links", []) if x["href"].startswith("http")}]
                F.add("mittel", "Rechtliches", f"{kind} nicht auf allen Seiten verlinkt ({n} von {total})", target,
                      lang=L, pages=no_link[:50], merge=False)
            if target in pages:
                txt = " ".join(b["t"] for b in pages[target].get("blocks", []) if b["zone"] == "main")
                dl, conf = detect_lang(txt)
                if dl and dl != L and conf > 0.6 and len(txt) > 200:
                    F.add("kritisch", "Rechtliches", f"{kind} ist auf {LANG_NAME.get(dl, dl)} statt {LANG_NAME.get(L, L)}",
                          target, lang=L, url=target)

    # ------------------------------------------------ 8. viewports
    vp_recs = [load_json(f) for f in glob.glob(os.path.join(wd, "viewports", "*.json"))]
    vp_summary = defaultdict(dict)
    for r in vp_recs:
        if r["url"] not in pages:
            continue
        L = pages[r["url"]]["lang_cfg"]
        issues = []
        if r.get("error"):
            issues.append("Ladefehler")
        elif not render_degraded:
            if r.get("scrollWidth", 0) > r.get("innerWidth", 0) + 2:
                issues.append("horizontales Scrollen")
                offs = ", ".join(f"{o['el']} bis {o['right']}px" for o in r.get("offenders", [])[:3]) or "Element nicht eindeutig"
                F.add("mittel", "Darstellung", f"Inhalt breiter als Bildschirm ({r['viewport']} {r['width']}px)",
                      "", lang=L, url=r["url"], screenshot=r.get("screenshot"),
                      evidence=[f"{r['url']}: Seite {r['scrollWidth']}px breit – {offs}"])
            if r.get("nav_links_total", 0) >= 2 and r.get("nav_links_visible", 0) < 2 and \
                    r.get("nav_links_visible_after_toggle", 0) < 2:
                issues.append("Navigation")
                F.add("kritisch", "Darstellung", f"Navigation auf {r['viewport']} nicht erreichbar",
                      "Kein Menü-Button gefunden" if not r.get("toggle") else f"Menü-Button «{r['toggle']}» öffnet keine sichtbaren Links",
                      lang=L, url=r["url"], screenshot=r.get("screenshot_menu") or r.get("screenshot"))
            for img in r.get("broken_images", [])[:5]:
                if urlsplit(img).hostname not in blocked_res:
                    issues.append("Bild")
                    F.add("mittel", "Darstellung", "Bild wird nicht angezeigt", img[:160], lang=L, url=r["url"])
        vp_summary[r["url"]][r["viewport"]] = {"issues": issues, "screenshot": r.get("screenshot")}

    # ------------------------------------------------ 9. forms
    inv = load_json(os.path.join(wd, "forms_inventory.json"), [])
    fres = load_json(os.path.join(wd, "forms_results.json"), {})
    for it in inv:
        r = fres.get(it["id"])
        it["result"] = r
        if not it["submit"]:
            continue
        if not r:
            F.add("hinweis", "Formulare", "Formular nicht getestet", f"{it['type']}", lang=it["lang"], url=it["test_page"], merge=False)
        elif r["result"] == "fehlgeschlagen":
            F.add("kritisch", "Formulare", f"Formular funktioniert nicht ({it['type']})", r.get("detail", ""),
                  lang=it["lang"], pages=it["pages"][:30], screenshot=r.get("screenshot"), evidence=r.get("filled", []), merge=False)
        elif r["result"] == "unklar":
            F.add("mittel", "Formulare", f"Formular-Ergebnis unklar ({it['type']})", r.get("detail", ""),
                  lang=it["lang"], pages=it["pages"][:30], screenshot=r.get("screenshot"), merge=False)
        if r and it["lang"] and multi:
            fl = lm.lang_of(r.get("final_url", "")) if r.get("url_changed") and r["result"] == "erfolgreich" else None
            if fl and fl != it["lang"]:
                F.add("mittel", "Formulare", f"Formular leitet nach dem Absenden auf {LANG_NAME.get(fl, fl)}-Seite weiter",
                      r["final_url"], lang=it["lang"], url=it["test_page"], screenshot=r.get("screenshot"))
            for t in r.get("success_text", []):
                for X in others[it["lang"]]:
                    if X != it["lang"] and foreign_strength(t, X, it["lang"]) == "strong":
                        F.add("mittel", "Übersetzung", "Formular-Meldung nicht übersetzt", t[:160], lang=it["lang"],
                              url=it["test_page"], screenshot=r.get("screenshot"))
                        break

    # ------------------------------------------------ 10. share links (share_links.py)
    share = load_json(os.path.join(wd, "share.json"))
    if share:
        for rec in share["links"]:
            for sev, title, detail in rec["problems"]:
                F.add(sev, "Teilen-Links", title, "", lang=rec["lang"], pages=rec["pages"],
                      evidence=[f"«{rec['label'] or rec['platform']}»: {detail}"] if detail else None)
        for t in share["targets"]:
            L = t["langs"][0] if len(t["langs"]) == 1 else None
            src_pages = sorted({p for rec in share["links"] if rec["href"] in t["links"] for p in rec["pages"]})
            for sev, title, detail in t["problems"]:
                F.add(sev, "Teilen-Links", title, "", lang=L, pages=src_pages,
                      evidence=[f"{t['url']} ({', '.join(t['platforms'])}){' – ' + detail if detail else ''}"])
            if t.get("blocked") or (t.get("preview") or {}).get("blocked"):
                unverifiable["In der Sandbox blockiert (geteilte Links)"].append(t["url"])
        for L, missing in share.get("missing_platforms", []):
            F.add("hinweis", "Teilen-Links", f"Teilen-Buttons fehlen, die es in einer anderen Sprache gibt: {', '.join(missing)}",
                  "", lang=L, pages=by_lang[L][:1])

    # ------------------------------------------------ 11. sitemap coverage
    sm = set(st.get("sitemap", []))
    if sm:
        not_in = [u for u in pages if u not in sm]
        if not_in:
            F.add("hinweis", "Technik", f"{len(not_in)} Seiten fehlen in der sitemap.xml", "", pages=sorted(not_in)[:60])
        orphans = [u for u in pages if u in sm and not inlinks.get(u)]
        if orphans:
            F.add("hinweis", "Links", f"{len(orphans)} Seiten sind nirgends verlinkt (nur in der Sitemap)", "", pages=sorted(orphans)[:60])
    else:
        F.add("hinweis", "Technik", "Keine sitemap.xml gefunden", "")

    # ------------------------------------------------ review overrides (manual pass by Claude/user)
    review = load_json(os.path.join(wd, "review.json"), {})
    findings = [f for f in F.sorted() if f["id"] not in set(review.get("suppress", []))]
    for extra in review.get("add", []):
        extra.setdefault("id", hashlib.sha1(repr(extra).encode()).hexdigest()[:10])
        extra.setdefault("pages", []); extra.setdefault("evidence", []); extra.setdefault("screenshots", [])
        extra["manual"] = True
        findings.append(extra)
    for f in findings:
        f["comment"] = review.get("comments", {}).get(f["id"], "")

    out = {
        "site": ", ".join(sorted({l["host"] for l in cfg["languages"]})),
        "languages": langs, "source_language": src,
        "viewports": [v["name"] + f" ({v['width']}px)" for v in cfg["viewports"]],
        "pages_crawled": len(all_pages), "pages_html": len(pages),
        "pages_per_lang": {l: len(by_lang[l]) for l in langs},
        "crawl_finished": st.get("finished", False), "crawl_truncated": st.get("truncated", 0),
        "max_depth": max((p.get("depth", 0) for p in all_pages.values() if p.get("depth", 0) < 99), default=0),
        "render_degraded": render_degraded, "blocked_resource_hosts": blocked_res,
        "unverifiable": {k: sorted(set(v)) for k, v in unverifiable.items()},
        "groups": groups, "legal": legal, "og_table": og_table, "forms": inv,
        "viewport_summary": vp_summary, "findings": findings, "share": share,
        "suppressed": len(review.get("suppress", [])),
    }
    save_json(os.path.join(wd, "findings.json"), out)
    from collections import Counter
    c = Counter(f["sev"] for f in findings)
    print(f"{len(findings)} Befunde: {dict(c)} | Seiten: {len(pages)} | Zeilen in Matrix: {len(groups)}"
          f"{' | DARSTELLUNG NICHT AUSSAGEKRÄFTIG (blockierte Ressourcen)' if render_degraded else ''}")
    for f in findings[:40]:
        print(f"  [{f['id']}] {f['sev']:8} {f['cat']:15} {f['lang'] or '-':3} {f['title'][:70]} ({len(f['pages'])} Seiten)")


if __name__ == "__main__":
    main(sys.argv[1])
