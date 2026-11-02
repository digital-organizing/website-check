#!/usr/bin/env python3
"""Render every crawled page at every configured viewport.

Usage: python viewports.py WORKDIR [--time-budget SECONDS]
Per page × viewport: horizontal overflow (+ offending elements), navigation reachable
(opens burger menus), broken images, full-page screenshot (JPEG).
Writes WORKDIR/viewports/<id>_<viewport>.json and WORKDIR/screens/<id>_<viewport>.jpg. Resumable.
"""
import argparse
import glob
import hashlib
import os
import time

from playwright.sync_api import sync_playwright

from common import UA, load_json, save_json, launch_browser

COOKIE_JS = r"""() => {
  const re = /^(alle\s+)?(cookies\s+)?(akzeptieren|annehmen|zulassen|zustimmen|einverstanden|verstanden|ok|accept( all)?( cookies)?|allow all|tout accepter|accepter( et fermer)?|j'accepte|accetta( tutti)?|accetto|d'accordo)$/i;
  for (const b of document.querySelectorAll('button, a, [role=button]')) {
    const t = (b.innerText || '').trim();
    if (t && t.length < 40 && re.test(t)) { b.click(); return t; }
  }
  return null;
}"""

MEASURE = r"""(W) => {
  // W = configured device width; with mobile emulation window.innerWidth grows to fit wide content
  const res = {scrollWidth: document.documentElement.scrollWidth, innerWidth: W, offenders: [], broken_images: []};
  const clipped = el => { for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e).overflowX; if (o === 'hidden' || o === 'auto' || o === 'scroll' || o === 'clip') return true; } return false; };
  const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  if (res.scrollWidth > W + 2) {
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > W + 2 && getComputedStyle(el).position !== 'fixed' && !clipped(el)) {
        res.offenders.push({el: desc(el), right: Math.round(r.right), text: (el.innerText || '').trim().slice(0, 60)});
        if (res.offenders.length >= 8) break;
      }
    }
  }
  document.querySelectorAll('img').forEach(i => { if (i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src))
      res.broken_images.push(i.currentSrc || i.src); });
  const LANG = /^(de|fr|it|en|rm|deutsch|français|italiano|english)$/i;
  const navLinks = [...new Set(document.querySelectorAll('header a[href], nav a[href], [class*=nav] a[href], [class*=menu] a[href]'))]
      .filter(a => !LANG.test((a.innerText || a.textContent || '').trim()) && !a.closest('[class*=lang],[class*=language],[class*=wpml],[class*=weglot]')
                   && !a.closest('footer, [class*=footer]'));  // footer links are not the menu
  res.nav_links_total = navLinks.length;
  res.nav_links_visible = navLinks.filter(a => { const r = a.getBoundingClientRect(); const s = getComputedStyle(a);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && r.top < window.innerHeight && r.left < W; }).length;
  return res;
}"""

TOGGLE_JS = r"""() => {
  const cands = [...document.querySelectorAll('button, [role=button], a, label')].filter(b => {
    const s = ((b.getAttribute('aria-label') || '') + ' ' + (b.className || '') + ' ' + (b.id || '') + ' ' + (b.innerText || '')).toString().toLowerCase();
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top < 200 && /(menu|menü|burger|hamburger|nav-toggle|navbar-toggler|toggle-nav|mobile-nav|open-nav|navigation)/.test(s);
  });
  if (!cands.length) return null;
  cands[0].click(); return (cands[0].getAttribute('aria-label') || cands[0].className || cands[0].tagName).toString().slice(0, 60);
}"""


def fid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--time-budget", type=int, default=0)
    a = ap.parse_args()
    wd = a.workdir
    cfg = load_json(os.path.join(wd, "config.json"))
    os.makedirs(os.path.join(wd, "viewports"), exist_ok=True)
    os.makedirs(os.path.join(wd, "screens"), exist_ok=True)
    pages = [load_json(f) for f in glob.glob(os.path.join(wd, "pages", "*.json"))]
    pages = [p for p in pages if p.get("status") and p["status"] < 400 and "html" in p.get("content_type", "")
             and p.get("final_url", p["url"]) == p["url"]]
    t0, n = time.time(), 0
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        for vp in cfg["viewports"]:
            ctx = browser.new_context(user_agent=UA, viewport={"width": vp["width"], "height": vp["height"]},
                                      is_mobile=vp["width"] < 800, has_touch=vp["width"] < 800)
            cookie_done = set()
            for p in sorted(pages, key=lambda x: x["url"]):
                out = os.path.join(wd, "viewports", f"{fid(p['url'])}_{vp['name']}.json")
                if os.path.exists(out):
                    continue
                if a.time_budget and time.time() - t0 > a.time_budget:
                    print("Zeitbudget erreicht – erneut starten zum Fortsetzen.", flush=True)
                    browser.close()
                    return
                page = ctx.new_page()
                rec = {"url": p["url"], "viewport": vp["name"], "width": vp["width"]}
                try:
                    page.goto(p["url"], wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    host = p["url"].split("/")[2]
                    if host not in cookie_done:
                        rec["cookie_banner"] = page.evaluate(COOKIE_JS)
                        cookie_done.add(host)
                        page.wait_for_timeout(600)
                    rec.update(page.evaluate(MEASURE, vp["width"]))
                    shot = os.path.join(wd, "screens", f"{fid(p['url'])}_{vp['name']}.jpg")
                    page.screenshot(path=shot, full_page=True, type="jpeg", quality=45, timeout=30000)
                    rec["screenshot"] = os.path.relpath(shot, wd)
                    if rec["nav_links_total"] >= 2 and rec["nav_links_visible"] < 2:  # < 2 menu links: one-pager with logo only
                        rec["toggle"] = page.evaluate(TOGGLE_JS)
                        if rec["toggle"]:
                            page.wait_for_timeout(800)
                            after = page.evaluate(MEASURE, vp["width"])
                            rec["nav_links_visible_after_toggle"] = after["nav_links_visible"]
                            shot2 = shot.replace(".jpg", "_menu.jpg")
                            page.screenshot(path=shot2, type="jpeg", quality=45)
                            rec["screenshot_menu"] = os.path.relpath(shot2, wd)
                except Exception as e:
                    rec["error"] = str(e).split("\n")[0][:300]
                page.close()
                save_json(out, rec)
                n += 1
                if n % 10 == 0:
                    print(f"{n} Seiten×Viewports fertig", flush=True)
            ctx.close()
        browser.close()
    print(f"Viewport-Prüfung abgeschlossen ({n} neu).")


if __name__ == "__main__":
    main()
