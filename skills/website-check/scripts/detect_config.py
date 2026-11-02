#!/usr/bin/env python3
"""Propose a language configuration for a site.

Usage: python detect_config.py --out WORKDIR/config.json https://example.ch [https://exemple.ch ...]

Reads <html lang>, hreflang alternates and language-switcher links from the given
start page(s) and writes a proposal. The proposal MUST be confirmed with the user
before crawling – language setups vary a lot (separate domains, /de/ omitted, ...).
"""
import argparse
import re
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from common import UA, bare_host, label_lang, save_json, normalize, launch_browser

JS = r"""() => {
  const out = {lang: document.documentElement.lang || '', alternates: [], switch: []};
  document.querySelectorAll('link[rel=alternate][hreflang]').forEach(l =>
     out.alternates.push({hreflang: l.hreflang, href: l.href}));
  document.querySelectorAll('a[href]').forEach(a => {
     const t = (a.innerText || a.textContent || '').trim();
     const cls = (a.className + ' ' + (a.parentElement ? a.parentElement.className : '')).toString().toLowerCase();
     out.switch.push({href: a.href, text: t.slice(0, 40), hreflang: a.hreflang || a.lang || '',
                      langish: /lang|language|wpml|weglot|polylang|switch|locale/.test(cls)});
  });
  return out;
}"""


def prefix_for(url, code):
    path = urlsplit(url).path or "/"
    seg = path.strip("/").split("/")[0].lower()
    if seg in (code, f"{code}-ch", f"{code}_ch"):
        return "/" + path.strip("/").split("/")[0] + "/"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    found = {}  # code -> start url
    notes = []
    with sync_playwright() as p:
        b = launch_browser(p)
        ctx = b.new_context(user_agent=UA)
        for u in a.urls:
            pg = ctx.new_page()
            pg.goto(u, wait_until="domcontentloaded", timeout=45000)
            try:
                pg.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            d = pg.evaluate(JS)
            own = (d["lang"] or "").split("-")[0].lower()
            if own:
                found.setdefault(own, normalize(pg.url))
            else:
                notes.append(f"{u}: kein <html lang> gesetzt")
            for alt in d["alternates"]:
                code = alt["hreflang"].split("-")[0].lower()
                if code and code != "x":
                    found.setdefault(code, normalize(alt["href"]))
            for s in d["switch"]:
                code = (s["hreflang"].split("-")[0].lower() if s["hreflang"] else None) or \
                       (label_lang(s["text"]) if (s["langish"] or len(s["text"]) <= 12) else None)
                if code and s["href"].startswith("http"):
                    found.setdefault(code, normalize(s["href"]))
            pg.close()
        b.close()

    langs = []
    for code, start in found.items():
        host = bare_host(urlsplit(start).hostname)
        langs.append({"code": code, "start": start, "host": host, "prefix": prefix_for(start, code)})

    # sites that omit the default-language prefix: several langs on one host, one of them without prefix
    by_host = {}
    for l in langs:
        by_host.setdefault(l["host"], []).append(l)
    for host, ls in by_host.items():
        empties = [l for l in ls if not l["prefix"]]
        if len(empties) > 1:
            notes.append(f"{host}: mehrere Sprachen ohne Pfad-Präfix ({', '.join(l['code'] for l in empties)}) – "
                         "Zuordnung muss manuell geklärt werden (evtl. Query-Parameter oder Cookie-basiert).")

    order = {"de": 0, "fr": 1, "it": 2, "en": 3}
    langs.sort(key=lambda l: order.get(l["code"], 9))
    cfg = {
        "languages": langs,
        "source_language": "de" if any(l["code"] == "de" for l in langs) else (langs[0]["code"] if langs else None),
        "extra_internal_hosts": [],
        "viewports": [{"name": "Mobile", "width": 375, "height": 812},
                      {"name": "Tablet", "width": 768, "height": 1024},
                      {"name": "Desktop", "width": 1440, "height": 900}],
        "max_pages": 800,
        "form_test": {"email": "hallo@digitalorganizing.ch", "first_name": "Test",
                      "last_name": "d/o Website-Check",
                      "message": "Automatischer Test von digital/organizing – bitte ignorieren und löschen."},
        "notes": notes,
    }
    save_json(a.out, cfg)
    print(f"Konfigurationsvorschlag → {a.out}")
    for l in langs:
        print(f"  {l['code']}: start={l['start']}  host={l['host']}  prefix='{l['prefix']}'")
    for n in notes:
        print("  HINWEIS:", n)
    if len(langs) <= 1:
        print("  Nur eine Sprache erkannt. Falls weitere existieren, Start-URLs zusätzlich angeben.")


if __name__ == "__main__":
    main()
