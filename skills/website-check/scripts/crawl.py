#!/usr/bin/env python3
"""Crawl every reachable internal page in every configured language.

Usage: python crawl.py WORKDIR [--time-budget SECONDS]

Seeds: the start URL of each language + all URLs from sitemap.xml (+ robots.txt Sitemap: lines).
Follows *every* internal link found in the DOM – including hidden ones in mobile menus,
footers, accordions – not just the visible navigation, so deep pages get covered.
Resumable: state lives in WORKDIR/crawl_state.json, one JSON per page in WORKDIR/pages/.
Run it in the background and poll WORKDIR/crawl.log.
"""
import argparse
import hashlib
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import urlsplit, urljoin

import requests
from playwright.sync_api import sync_playwright

from common import UA, SKIP_EXT, LangMap, load_json, save_json, normalize, bare_host, launch_browser

EXTRACT = r"""() => {
  const zoneOf = el => {
    for (let e = el; e; e = e.parentElement) {
      const t = e.tagName.toLowerCase(), c = ((e.className || '') + ' ' + (e.id || '')).toString().toLowerCase();
      if (t === 'footer' || /footer/.test(c)) return 'footer';
      if (t === 'nav' || t === 'header' || /(^|\s|-)(nav|menu|header)/.test(c)) return 'nav';
    }
    return 'main';
  };
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = {lang: document.documentElement.lang || '', title: document.title || '',
    meta: {}, canonical: '', robots: '', alternates: [], links: [], forms: [], iframes: [],
    ids: [], blocks: [], attrs: [], h1: []};
  document.querySelectorAll('meta[property], meta[name]').forEach(m => {
    const k = (m.getAttribute('property') || m.getAttribute('name') || '').toLowerCase();
    if (/^(og:|twitter:|description$|robots$)/.test(k) && !(k in out.meta)) out.meta[k] = m.getAttribute('content') || '';
  });
  const c = document.querySelector('link[rel=canonical]'); if (c) out.canonical = c.href;
  document.querySelectorAll('link[rel=alternate][hreflang]').forEach(l => out.alternates.push({hreflang: l.hreflang, href: l.href}));
  document.querySelectorAll('a').forEach(a => {
    const raw = a.getAttribute('href');
    const cls = (a.className + ' ' + (a.parentElement ? a.parentElement.className : '') + ' ' +
                 (a.closest('[class*=lang],[class*=language],[class*=wpml],[class*=weglot],[class*=polylang]') ? 'langbox' : '')).toString().toLowerCase();
    out.links.push({href: a.href || '', raw: raw, text: clean(a.innerText || a.textContent || a.getAttribute('aria-label') || a.title ||
        (a.querySelector('img') ? a.querySelector('img').alt : '')).slice(0, 120),
      zone: zoneOf(a), visible: visible(a), hreflang: a.hreflang || a.lang || '',
      langish: /lang|language|wpml|weglot|polylang|locale|langbox/.test(cls), target: a.target || ''});
  });
  document.querySelectorAll('[id]').forEach(e => out.ids.push(e.id));
  document.querySelectorAll('a[name]').forEach(e => out.ids.push(e.getAttribute('name')));
  document.querySelectorAll('h1').forEach(h => out.h1.push(clean(h.textContent)));
  document.querySelectorAll('form').forEach((f, i) => {
    const fields = [];
    f.querySelectorAll('input, select, textarea').forEach(el => {
      const type = (el.type || el.tagName).toLowerCase();
      if (['hidden'].includes(type)) return;
      let label = '';
      if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) label = clean(l.textContent); }
      if (!label && el.closest('label')) label = clean(el.closest('label').textContent);
      fields.push({tag: el.tagName.toLowerCase(), type, name: el.name || '', id: el.id || '', required: el.required || el.getAttribute('aria-required') === 'true',
                   label: label.slice(0, 150), placeholder: el.placeholder || '', options: el.tagName === 'SELECT' ? el.options.length : 0});
    });
    const buttons = [...f.querySelectorAll('button, input[type=submit]')].map(b => clean(b.innerText || b.value || b.getAttribute('aria-label')));
    const html = f.innerHTML.toLowerCase();
    out.forms.push({index: i, id: f.id || '', action: f.action || '', method: (f.method || 'get').toLowerCase(), fields, buttons,
      captcha: /recaptcha|hcaptcha|turnstile|friendly-captcha|frc-captcha/.test(html + document.head.innerHTML.toLowerCase()),
      role: f.getAttribute('role') || '', cls: (f.className || '').toString().slice(0, 80)});
  });
  document.querySelectorAll('iframe').forEach(f => out.iframes.push(f.src || ''));
  // text blocks: leaf-ish block elements, textContent so hidden menus/accordions are included
  const seen = new Set();
  const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|SVG|CODE|PRE|HTML|HEAD|BODY|TEMPLATE)$/;
  const HASBLOCK = 'p,li,h1,h2,h3,h4,h5,h6,div,td,section,article,ul,ol,table,form,header,footer,nav';
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (SKIP.test(el.tagName) || el.closest('script,style,noscript,svg,code,pre,template')) continue;
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
    if (clean(own).length < 2) continue;
    // containers with nested blocks: only their own loose text; leaf blocks: full text incl. inline markup
    const t = el.querySelector(HASBLOCK) ? clean(own) : clean(el.textContent);
    if (t.length < 2 || seen.has(t)) continue; seen.add(t);
    out.blocks.push({t: t.slice(0, 400), zone: zoneOf(el)});
  }
  document.querySelectorAll('img[alt], [placeholder], [aria-label], input[type=submit][value], [title]').forEach(el => {
    for (const a of ['alt', 'placeholder', 'aria-label', 'value', 'title']) {
      if (a === 'value' && el.type !== 'submit') continue;
      const v = clean(el.getAttribute(a)); if (v && v.length > 1) out.attrs.push({a, v: v.slice(0, 200)});
    }
  });
  out.text_len = clean(document.body ? document.body.innerText : '').length;
  return out;
}"""


def fid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def sitemap_urls(host, log, scheme="https"):
    urls, todo, done = set(), [], set()
    try:
        r = requests.get(f"{scheme}://{host}/robots.txt", headers={"User-Agent": UA}, timeout=15)
        todo += re.findall(r"(?im)^sitemap:\s*(\S+)", r.text) if r.ok else []
    except requests.RequestException:
        pass
    todo += [f"{scheme}://{host}/sitemap.xml", f"{scheme}://{host}/sitemap_index.xml"]
    while todo and len(done) < 40:
        sm = todo.pop(0)
        if sm in done:
            continue
        done.add(sm)
        try:
            r = requests.get(sm, headers={"User-Agent": UA}, timeout=20)
            if not r.ok or b"<" not in r.content[:200]:
                continue
            root = ET.fromstring(r.content)
        except Exception:
            continue
        is_index = root.tag.endswith("sitemapindex")
        for entry in root:  # <url> or <sitemap>; ignore nested image:loc etc.
            for child in entry:
                if child.tag.split("}")[-1] == "loc" and child.text:
                    (todo.append(child.text.strip()) if is_index else urls.add(child.text.strip()))
    log(f"Sitemap {host}: {len(urls)} URLs")
    return urls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--time-budget", type=int, default=0, help="stop after N seconds (resume later)")
    a = ap.parse_args()
    wd = a.workdir
    cfg = load_json(os.path.join(wd, "config.json"))
    lm = LangMap(cfg)
    os.makedirs(os.path.join(wd, "pages"), exist_ok=True)
    logf = open(os.path.join(wd, "crawl.log"), "a", encoding="utf-8")

    def log(msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    st_path = os.path.join(wd, "crawl_state.json")
    st = load_json(st_path)
    if not st:
        queue = [[normalize(l["start"]), 0, "Start"] for l in cfg["languages"]]
        sm = set()
        for origin in sorted({urlsplit(l["start"]).scheme + "://" + urlsplit(l["start"]).netloc for l in cfg["languages"]}):
            for u in sitemap_urls(urlsplit(origin).netloc, log, urlsplit(origin).scheme):
                if lm.is_internal(u):
                    sm.add(normalize(u))
        queue += [[u, 99, "Sitemap"] for u in sorted(sm)]
        st = {"queue": queue, "seen": [q[0] for q in queue], "done": [], "sitemap": sorted(sm),
              "inlinks": {}, "query_variants": {}}
    seen = set(st["seen"])
    done = set(st["done"])
    qv = defaultdict(int, st["query_variants"])
    inlinks = defaultdict(list, st["inlinks"])
    max_pages = cfg.get("max_pages", 800)
    t0 = time.time()

    with sync_playwright() as p:
        browser = launch_browser(p)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900}, locale="de-CH")
        while st["queue"]:
            if a.time_budget and time.time() - t0 > a.time_budget:
                log("Zeitbudget erreicht – Crawl pausiert, erneut starten zum Fortsetzen.")
                break
            if len(done) >= max_pages:
                log(f"max_pages ({max_pages}) erreicht – Rest der Queue ({len(st['queue'])}) nicht gecrawlt.")
                st["truncated"] = len(st["queue"])
                break
            url, depth, via = st["queue"].pop(0)
            if url in done:
                continue
            page = ctx.new_page()
            console, failed = [], []
            page.on("console", lambda m: console.append(m.text[:300]) if m.type == "error" else None)
            page.on("requestfailed", lambda r: failed.append({"url": r.url[:300], "type": r.resource_type,
                                                              "error": (r.failure or "")[:100]}))
            page.on("response", lambda r: failed.append({"url": r.url[:300], "type": r.request.resource_type, "status": r.status})
                    if r.status >= 400 and r.request.resource_type in ("image", "script", "stylesheet", "font", "media") else None)
            rec = {"url": url, "depth": depth, "via": via, "lang_cfg": lm.lang_of(url)}
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                rec["status"] = resp.status if resp else None
                rec["final_url"] = normalize(page.url)
                ctype = (resp.headers.get("content-type", "") if resp else "")
                rec["content_type"] = ctype
                if "html" in ctype and lm.is_internal(page.url):
                    rec.update(page.evaluate(EXTRACT))
            except Exception as e:
                rec["status"] = None
                rec["error"] = str(e).split("\n")[0][:300]
            rec["console_errors"] = console[:20]
            rec["failed_resources"] = failed[:30]
            page.close()
            save_json(os.path.join(wd, "pages", fid(url) + ".json"), rec)
            done.add(url)

            for ln in rec.get("links", []):
                h = ln["href"]
                if not h.startswith("http"):
                    continue
                n = normalize(h)
                if n != url:
                    inlinks[n].append(url) if len(inlinks[n]) < 50 else None
                if not lm.is_internal(n) or SKIP_EXT.search(urlsplit(n).path) or n in seen:
                    continue
                sp = urlsplit(n)
                if sp.query:
                    key = sp.netloc + sp.path
                    if qv[key] >= 5:
                        continue
                    qv[key] += 1
                seen.add(n)
                st["queue"].append([n, depth + 1 if depth < 99 else 1, url])
            if len(done) % 10 == 0:
                log(f"{len(done)} Seiten fertig, Queue {len(st['queue'])}")
                st.update(seen=sorted(seen), done=sorted(done), inlinks=inlinks, query_variants=qv)
                save_json(st_path, st)
        browser.close()
    st.update(seen=sorted(seen), done=sorted(done), inlinks=inlinks, query_variants=qv)
    st["finished"] = not st["queue"]
    save_json(st_path, st)
    log(f"Crawl {'abgeschlossen' if st['finished'] else 'unterbrochen'}: {len(done)} Seiten, Queue {len(st['queue'])}")


if __name__ == "__main__":
    main()
