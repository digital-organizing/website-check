#!/usr/bin/env python3
"""Browser and device test: the same pages in Chromium, Firefox (Gecko) and WebKit (Safari), desktop and mobile.

Usage:
  python browsers.py WORKDIR [--pages main|all] [--max-pages N] [--extra small,dark,slow,zoom,inapp]
                             [--combos id,id] [--time-budget SECONDS]
  python browsers.py WORKDIR --urls https://beispiel.ch/ https://beispiel.ch/fr/   # ad hoc, no crawl needed

Practically every browser runs on one of three engines: Chromium (Chrome, Edge, Brave, Opera, Samsung
Internet), Gecko (Firefox) and WebKit (Safari – and on iPhone/iPad *every* browser, Chrome and Firefox too).

Per page × combination (never submits a form):
  status, uncaught JavaScript errors, failed resources, horizontal overflow (+ offending elements),
  broken images, web fonts really loaded (document.fonts, only families in use), language switcher
  visible (directly or behind the burger menu), forms incl. Tally/HubSpot iframes (fields visible, tap a
  text field by placeholder and type, submit button inside the frame), first accordion opens, share
  buttons visible and tappable, screenshot of the first screen (not full page: fixed/sticky elements
  end up in the wrong place on full-page screenshots).

Writes WORKDIR/browsers/<page-id>_<combo>.json (resumable), WORKDIR/browsers.json (summary for
analyze.py/report.py) and WORKDIR/screens/browser_<page-id>_<combo>.jpg.
Engines that cannot be started (not installed, missing libraries) are skipped and listed in
browsers.json → meta.unavailable, so the report can say what was *not* tested.
"""
import argparse
import glob
import hashlib
import os
import shutil
import sys
import tempfile
import time

from common import (UA, LangMap, load_json, save_json, normalize, launch_browser, scroll_through,
                    find_browsers_path, firefox_profile)

UA_SUFFIX = " d-o-website-check"

# id, label, engine, device class, context options (device descriptor name or dict), group
STANDARD = [
    ("chrome-desktop", "Chrome/Edge Desktop", "chromium", "desktop", {"desc": "Desktop Chrome", "viewport": {"width": 1440, "height": 900}}),
    ("chrome-android", "Chrome Android (Pixel 7)", "chromium", "mobile", {"desc": "Pixel 7"}),
    ("firefox-desktop", "Firefox Desktop", "firefox", "desktop", {"desc": "Desktop Firefox", "viewport": {"width": 1440, "height": 900}}),
    # Playwright's Firefox has no isMobile (meta viewport is ignored): phone width, touch and UA instead
    ("firefox-android", "Firefox Android", "firefox", "mobile",
     {"viewport": {"width": 412, "height": 839}, "device_scale_factor": 2.625, "has_touch": True,
      "user_agent": "Mozilla/5.0 (Android 14; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0"}),
    ("safari-mac", "Safari macOS", "webkit", "desktop", {"desc": "Desktop Safari", "viewport": {"width": 1440, "height": 900}}),
    ("safari-iphone", "Safari iPhone 15", "webkit", "mobile", {"desc": "iPhone 15"}),
    ("safari-ipad", "Safari iPad", "webkit", "tablet", {"desc": "iPad (gen 7)"}),
]
IG_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
         "Instagram 380.0.0.0 (iPhone15,4; iOS 18_5; de_CH; de; scale=3.00; 1179x2556)")
FB_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/AP2A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
         "Chrome/130.0.0.0 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/480.0.0.0;]")
LI_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
         "[LinkedInApp]/9.30")
EXTRA = {
    "small": [
        ("safari-iphone-se", "Safari iPhone SE (375 px)", "webkit", "mobile", {"desc": "iPhone SE (3rd gen)"}),
        ("chrome-320", "Chrome 320 px", "chromium", "mobile",
         {"desc": "Pixel 7", "viewport": {"width": 320, "height": 640}, "device_scale_factor": 2}),
    ],
    "dark": [
        ("chrome-android-dark", "Chrome Android Dark Mode", "chromium", "mobile", {"desc": "Pixel 7", "color_scheme": "dark"}),
        # automatic darkening of pages without dark styles (Chrome flag, Samsung Internet's default behaviour)
        ("chrome-android-forcedark", "Chrome Android automatisch abgedunkelt", "chromium-forcedark", "mobile",
         {"desc": "Pixel 7", "color_scheme": "dark"}),
    ],
    "slow": [
        ("chrome-android-3g", "Chrome Android 3G", "chromium", "mobile", {"desc": "Pixel 7", "throttle": True}),
    ],
    "zoom": [  # browser zoom shrinks the CSS viewport: 1440 px at 150 % = 960 CSS px, at 200 % = 720 CSS px
        ("chrome-zoom150", "Chrome Desktop 150 % Zoom", "chromium", "desktop",
         {"desc": "Desktop Chrome", "viewport": {"width": 960, "height": 600}, "device_scale_factor": 1.5}),
        ("chrome-zoom200", "Chrome Desktop 200 % Zoom", "chromium", "desktop",
         {"desc": "Desktop Chrome", "viewport": {"width": 720, "height": 450}, "device_scale_factor": 2}),
    ],
    "inapp": [  # approximation: real in-app browsers differ (no mailto: popup, limited downloads …)
        ("instagram-iphone", "Instagram-Browser iPhone (UA)", "webkit", "mobile", {"desc": "iPhone 15", "user_agent": IG_UA}),
        ("facebook-android", "Facebook-Browser Android (UA)", "chromium", "mobile", {"desc": "Pixel 7", "user_agent": FB_UA}),
        ("linkedin-iphone", "LinkedIn-Browser iPhone (UA)", "webkit", "mobile", {"desc": "iPhone 15", "user_agent": LI_UA}),
    ],
}
ENGINE_LABEL = {"chromium": "Chromium", "chromium-forcedark": "Chromium", "firefox": "Firefox (Gecko)", "webkit": "WebKit (Safari)"}

PROVIDERS = r"tally\.so|hsforms|hubspot|typeform|docs\.google\.com/forms|mailchimp|list-manage|cleverreach|jotform|formstack|raisenow|payrexx|fundraisingbox|twingle|mailerlite|brevo|sendinblue|getresponse|formbricks|forms\.office"

MEASURE_JS = r"""async (W) => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  const clipped = el => { for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e).overflowX; if (o === 'hidden' || o === 'auto' || o === 'scroll' || o === 'clip') return true; } return false; };
  const res = {scrollWidth: document.documentElement.scrollWidth, width: W, offenders: [], broken_images: []};
  if (res.scrollWidth > W + 2) {
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > W + 2 && getComputedStyle(el).position !== 'fixed' && !clipped(el)) {
        res.offenders.push({el: desc(el), right: Math.round(r.right), text: (el.innerText || '').trim().slice(0, 60)});
        if (res.offenders.length >= 6) break;
      }
    }
  }
  // content pushed past the LEFT edge is cut off without any scrollbar (scrollWidth does not see it)
  res.left_cut = [];
  for (const el of document.body.querySelectorAll('p,h1,h2,h3,h4,li,a,button,img,input,label')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.left < -4 && r.right > 0 && getComputedStyle(el).position !== 'fixed' && !clipped(el) && vis(el)) {
      res.left_cut.push({el: desc(el), left: Math.round(r.left), text: (el.innerText || el.alt || '').trim().slice(0, 60)});
      if (res.left_cut.length >= 4) break;
    }
  }
  document.querySelectorAll('img').forEach(i => { if (i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src) && vis(i))
      res.broken_images.push(i.currentSrc || i.src); });

  // fonts: only families that visible text actually uses; declared-but-unused faces stay "unloaded" legitimately
  try { await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 4000))]); } catch (e) {}
  const norm = f => (f || '').replace(/["']/g, '').trim().toLowerCase();
  const used = new Set();
  document.querySelectorAll('h1,h2,h3,p,a,button,li,label,input').forEach(el => {
    if (used.size < 12 && vis(el)) used.add(norm(getComputedStyle(el).fontFamily.split(',')[0])); });
  const faces = [...document.fonts];
  res.fonts = [...used].map(fam => { const fs = faces.filter(f => norm(f.family) === fam);
    return {family: fam, faces: fs.length, loaded: fs.some(f => f.status === 'loaded'), error: fs.some(f => f.status === 'error')}; })
    .filter(f => f.faces > 0);

  // first h1: does it wrap cleanly (no word wider than the screen)?
  const h1 = [...document.querySelectorAll('h1')].find(vis);
  if (h1) { const r = h1.getBoundingClientRect();
    res.h1 = {text: h1.innerText.trim().slice(0, 80), overflow: h1.scrollWidth > h1.clientWidth + 2 || r.right > W + 2 || r.left < -2}; }

  // text cut off by overflow:hidden (zoom, small screens)
  res.clipped_text = [];
  for (const el of document.querySelectorAll('h1,h2,h3,p,a,button,label,li')) {
    if (res.clipped_text.length >= 5) break;
    const s = getComputedStyle(el);
    if (!vis(el) || !(el.innerText || '').trim() || s.textOverflow === 'ellipsis' || s.webkitLineClamp !== 'none' && s.webkitLineClamp) continue;
    if ((s.overflow === 'hidden' || s.overflowY === 'hidden') && el.scrollHeight > el.clientHeight + 4 && el.clientHeight > 0)
      res.clipped_text.push(desc(el) + ': ' + el.innerText.trim().slice(0, 60));
  }

  // contrast of visible text against its effective background (meaningful for dark mode)
  const rgb = c => { const m = (c || '').match(/[\d.]+/g); return m ? m.map(Number) : null; };
  const lum = c => { const a = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]; };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const c = rgb(s.backgroundColor); if (c && (c.length < 4 || c[3] > 0.9)) return c; }
    return [255, 255, 255]; };
  res.low_contrast = [];
  for (const el of document.querySelectorAll('h1,h2,h3,p,a,button,label,li,span')) {
    if (res.low_contrast.length >= 8) break;
    if (!vis(el) || el.children.length > 2) continue;
    const t = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (t.length < 3) continue;
    const fg = rgb(getComputedStyle(el).color), bg = bgOf(el);
    if (!fg || !bg || (fg.length > 3 && fg[3] < 0.5)) continue;
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const big = parseFloat(getComputedStyle(el).fontSize) >= 24;
    if (ratio < (big ? 3 : 4.5)) res.low_contrast.push({text: t.slice(0, 50), ratio: Math.round(ratio * 10) / 10});
  }
  return res;
}"""

LANGSWITCH_JS = r"""(W) => {
  const LANG = /^(de|fr|it|en|rm|deutsch|français|francais|italiano|english|rumantsch)$/i;
  const all = [...document.querySelectorAll('a[href], button, [role=button], [class*=dropdown-toggle]')].filter(a =>
      LANG.test((a.innerText || a.textContent || '').trim()) || (a.hreflang && a.tagName === 'A') ||
      a.closest('[class*=lang],[class*=language],[class*=wpml],[class*=weglot],[class*=polylang],[class*=locale]'));
  const vis = a => { const r = a.getBoundingClientRect(); const s = getComputedStyle(a);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && +s.opacity !== 0; };
  const inside = a => { const r = a.getBoundingClientRect(); return r.left >= -1 && r.right <= W + 1; };
  return {found: all.length, visible: all.filter(vis).length, inside: all.filter(a => vis(a) && inside(a)).length,
          outside: all.filter(a => vis(a) && !inside(a)).map(a => (a.innerText || '').trim().slice(0, 20) + ' bis ' +
                   Math.round(a.getBoundingClientRect().right) + 'px').slice(0, 3)};
}"""

ACCORDION_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const skip = el => el.closest('header, nav, [class*=nav], [class*=menu], footer');
  const c = [...document.querySelectorAll('details > summary')].filter(e => vis(e) && !skip(e))[0] ||
            [...document.querySelectorAll('[aria-expanded="false"]')].filter(e => vis(e) && !skip(e) && !/menu|nav|lang/i.test(e.className + e.id))[0] ||
            [...document.querySelectorAll('[class*=accordion] [class*=toggle], [class*=accordion] [class*=trigger], [class*=accordion] [class*=head], ' +
                                          '[class*=faq] [class*=question], [class*=faq] [class*=toggle], .w-dropdown-toggle')].filter(e => vis(e) && !skip(e))[0];
  if (!c) return null;
  c.setAttribute('data-wc-acc', '1');
  return {el: c.tagName.toLowerCase() + '.' + String(c.className || '').split(' ')[0], text: (c.innerText || '').trim().slice(0, 50)};
}"""

ACC_STATE_JS = r"""() => { const c = document.querySelector('[data-wc-acc]'); if (!c) return null;
  const box = c.closest('details') || c.parentElement;
  return {h: document.body.scrollHeight, box: box.getBoundingClientRect().height,
          open: (c.closest('details') && c.closest('details').open) || c.getAttribute('aria-expanded') === 'true'}; }"""

SHARE_JS = r"""(W) => {
  const RX = /wa\.me|whatsapp|t\.me\/share|telegram\.me\/share|facebook\.com\/(sharer|share|dialog)|linkedin\.com\/(shareArticle|sharing|feed\/\?shareActive)|twitter\.com\/intent|x\.com\/intent|threema|bsky\.app\/intent|^mailto:.*(subject|body)=|^sms:/i;
  const out = [];
  document.querySelectorAll('a[href]').forEach(a => {
    const h = a.getAttribute('href') || '';
    if (!RX.test(h)) return;
    const r = a.getBoundingClientRect(), s = getComputedStyle(a);
    const visible = r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && +s.opacity !== 0;
    let tappable = false;
    if (visible) {
      a.scrollIntoView({block: 'center'});
      const r2 = a.getBoundingClientRect();
      const hit = document.elementFromPoint(r2.left + r2.width / 2, r2.top + r2.height / 2);
      tappable = !!hit && (hit === a || a.contains(hit) || hit.contains(a));
    }
    out.push({href: h.slice(0, 80), label: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 30),
              visible, inside: visible && r.right <= W + 1 && r.left >= -1, tappable,
              small: visible && (r.width < 24 || r.height < 24), w: Math.round(r.width), h: Math.round(r.height)});
  });
  window.scrollTo(0, 0);
  return out;
}"""

FORM_FIELDS_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const forms = [...document.querySelectorAll('form')].filter(f => vis(f) && f.querySelector('input, textarea'));
  const f = forms.find(f => f.querySelector('input[type=email], textarea')) || forms[0];
  const scope = f || document;
  const fields = [...scope.querySelectorAll('input, textarea, select')].filter(e => vis(e) && !['hidden', 'submit', 'button'].includes(e.type));
  const text = fields.find(e => (e.tagName === 'TEXTAREA' || ['text', 'email', ''].includes(e.type)) && !e.readOnly && !e.disabled &&
                                e.getAttribute('role') !== 'combobox' && !e.getAttribute('aria-haspopup') && e.placeholder) ||
               fields.find(e => (e.tagName === 'TEXTAREA' || ['text', 'email'].includes(e.type)) && !e.readOnly && !e.disabled &&
                                e.getAttribute('role') !== 'combobox' && !e.getAttribute('aria-haspopup'));
  const btns = [...scope.querySelectorAll('button[type=submit], input[type=submit], button:not([type])')].filter(vis);
  const btn = btns[btns.length - 1];
  const W = document.documentElement.clientWidth, br = btn && btn.getBoundingClientRect();
  if (text) text.setAttribute('data-wc-text', '1');
  if (btn) btn.setAttribute('data-wc-btn', '1');
  return {has_form: !!f, fields: fields.length, text_field: text ? (text.placeholder || text.name || text.type) : null,
          button: btn ? (btn.innerText || btn.value || '').trim().slice(0, 40) : null,
          button_inside: btn ? (br.left >= -1 && br.right <= W + 1 && br.width > 0) : null,
          doc_height: document.documentElement.scrollHeight};
}"""


GOTO_FORM_JS = r"""(prov) => {
  const rx = new RegExp(prov);
  const f = [...document.querySelectorAll('iframe, form')].find(e => e.tagName === 'IFRAME'
      ? rx.test(e.src || e.getAttribute('data-tally-src') || e.getAttribute('data-src') || '')
      : !!e.querySelector('input[type=email], textarea'));
  if (f) { f.scrollIntoView({block: 'center'}); return true; }
  window.scrollBy(0, 400); return false;
}"""


def fid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def combo_list(cfg, extras, only):
    """Standard combinations (config.json → browsers overrides by id or adds own entries) + extras."""
    std = [dict(zip(("id", "label", "engine", "device", "opts"), c), group="standard") for c in STANDARD]
    custom = cfg.get("browsers")
    if custom:  # e.g. ["chrome-desktop", "safari-iphone"] or full dicts {"id","label","engine","device","opts"}
        by_id = {c["id"]: c for c in std}
        std = [by_id[c] if isinstance(c, str) else {**c, "group": "standard"} for c in custom if isinstance(c, dict) or c in by_id]
    out = list(std)
    for x in extras:
        out += [dict(zip(("id", "label", "engine", "device", "opts"), c), group=x) for c in EXTRA.get(x, [])]
    if only:
        out = [c for c in out if c["id"] in only]
    return out


def select_pages(wd, cfg, mode, max_pages, lm):
    """main: start page per language + test pages of forms + thank-you pages + one page with share buttons per
    language. Browser differences are template-level, so these cover them; `all` multiplies the run time."""
    pages = [load_json(f) for f in glob.glob(os.path.join(wd, "pages", "*.json"))]
    html = {p["url"]: p for p in pages if p.get("status") and p["status"] < 400 and "html" in p.get("content_type", "")
            and p.get("final_url", p["url"]) == p["url"]}
    if mode == "all" and html:
        return sorted(html)[:max_pages]
    sel = []

    def add(u):
        u = normalize(u)
        if u not in sel and (not html or u in html):
            sel.append(u)
    for l in cfg.get("languages", []):
        if l.get("start"):
            add(l["start"])
    for it in load_json(os.path.join(wd, "forms_inventory.json"), []):
        add(it["test_page"])
        for r in it.get("redirects", []) + it.get("thank_you_live", []):
            add(r)
    for r in load_json(os.path.join(wd, "forms_results.json"), {}).values():
        if r.get("url_changed") and r.get("final_url") and lm.is_internal(r["final_url"]):
            add(r["final_url"])
    share = load_json(os.path.join(wd, "share.json"))
    if share:
        seen_l = set()
        for rec in share["links"]:
            if rec["lang"] not in seen_l and rec["pages"]:
                seen_l.add(rec["lang"])
                add(sorted(rec["pages"], key=len)[0])
    if not html:  # no crawl yet: start URLs only
        sel = [normalize(l["start"]) for l in cfg.get("languages", []) if l.get("start")]
    return sel[:max_pages]


def context_options(pw, combo):
    o = dict(combo["opts"])
    desc = o.pop("desc", None)
    throttle = o.pop("throttle", False)
    opts = {}
    if desc:
        opts.update({k: v for k, v in pw.devices[desc].items() if k != "default_browser_type"})
    opts.update(o)
    if combo["engine"] == "firefox":
        opts.pop("is_mobile", None)  # not supported by Playwright's Firefox
    opts["user_agent"] = (opts.get("user_agent") or UA) + UA_SUFFIX
    opts.setdefault("locale", "de-CH")
    return opts, throttle


class Engines:
    """Starts each engine once; remembers why one is unavailable instead of aborting the whole run."""

    def __init__(self, pw, wd):
        self.pw, self.wd, self.browsers, self.unavailable, self.versions = pw, wd, {}, {}, {}
        self.tmp = tempfile.mkdtemp(prefix="wc-ff-")

    def context(self, combo):
        eng = combo["engine"]
        if eng in self.unavailable:
            return None
        opts, _ = context_options(self.pw, combo)
        try:
            if eng == "firefox":
                # persistent context = profile that trusts the sandbox proxy CA; one copy per combination
                base = firefox_profile(self.wd if os.access(self.wd, os.W_OK) else self.tmp)
                prof = os.path.join(self.tmp, combo["id"])
                shutil.rmtree(prof, ignore_errors=True)
                shutil.copytree(base, prof)
                ctx = self.pw.firefox.launch_persistent_context(prof, **opts)
                try:
                    self.versions[eng] = ctx.browser.version
                except Exception:
                    pass
                return ctx
            if eng not in self.browsers:
                if eng == "chromium":
                    self.browsers[eng] = launch_browser(self.pw)
                elif eng == "chromium-forcedark":
                    self.browsers[eng] = launch_browser(self.pw, args=["--blink-settings=forceDarkModeEnabled=true",
                                                                         "--enable-features=WebContentsForceDark"])
                else:
                    self.browsers[eng] = self.pw.webkit.launch()
                self.versions[eng] = self.browsers[eng].version
            return self.browsers[eng].new_context(**opts)
        except Exception as e:
            msg = str(e).split("\n")[0][:200]
            if "Executable doesn't exist" in str(e):
                msg = "nicht installiert (siehe SKILL.md, Voraussetzungen)"
            elif "dependencies" in str(e).lower() or "missing" in str(e).lower():
                msg = f"Systembibliotheken fehlen (python -m playwright install-deps {eng})"
            self.unavailable[eng] = msg
            print(f"  {ENGINE_LABEL.get(eng, eng)} nicht verfügbar: {msg}", flush=True)
            return None

    def close(self):
        for b in self.browsers.values():
            try:
                b.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def provider_frame(page):
    import re
    for f in page.frames:
        if f != page.main_frame and re.search(PROVIDERS, f.url or ""):
            return f
    return None


def test_form(page, combo, rec, no_typing_hosts):
    """Fields visible, tap a text field (by placeholder, never a combobox) and type, submit button inside
    the frame. Never submits."""
    import re
    touch = combo["opts"].get("has_touch") or combo["device"] != "desktop"
    iframe_el = page.locator("iframe")
    frame, root, ifr, src = None, page, None, ""
    for i in range(min(iframe_el.count(), 10)):
        el = iframe_el.nth(i)
        src = (el.get_attribute("src") or el.get_attribute("data-tally-src") or "")
        if re.search(PROVIDERS, src):
            ifr = el
            break
    if ifr is not None:
        rec["form"] = {"kind": "iframe", "src": src.split("?")[0]}
        try:
            ifr.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(1500)
            frame = ifr.element_handle(timeout=5000).content_frame() or provider_frame(page)
            if frame is not None:
                try:
                    frame.wait_for_selector("input, textarea", timeout=15000, state="visible")
                    page.wait_for_timeout(1000)  # dynamicHeight embeds resize after rendering
                except Exception:
                    pass
                root = frame
            box = ifr.bounding_box()
            rec["form"]["iframe_box"] = {k: round(v) for k, v in box.items()} if box else None
        except Exception as e:
            rec["form"]["error"] = str(e).split("\n")[0][:150]
        if frame is None:
            rec["form"].update(loaded=False)
            return
    try:
        info = root.evaluate(FORM_FIELDS_JS)
    except Exception as e:
        rec.setdefault("form", {})["error"] = str(e).split("\n")[0][:150]
        return
    if ifr is None and not info["has_form"]:
        return  # no form on this page
    f = rec.setdefault("form", {"kind": "html"})
    f.update(loaded=info["fields"] > 0, fields=info["fields"], text_field=info["text_field"], button=info["button"],
             button_inside=info["button_inside"])
    if ifr is not None and f.get("iframe_box") and info.get("doc_height"):
        # dynamicHeight embeds: the iframe must be as tall as the form inside, otherwise the button is cut off
        f["iframe_cut"] = info["doc_height"] > f["iframe_box"]["height"] + 40
        f["iframe_wider_than_screen"] = f["iframe_box"]["x"] + f["iframe_box"]["width"] > combo_width(page) + 2
    if info["text_field"]:
        if any(h and h.startswith(f.get("src") or "-") for h in no_typing_hosts):
            f["typing"] = "übersprungen (Teil-Antworten aktiv)"
        else:
            el = root.locator("[data-wc-text]").first
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                el.tap(timeout=5000) if touch else el.click(timeout=5000)
                el.press_sequentially("Test", delay=30, timeout=5000)  # real key events into this field
                page.wait_for_timeout(300)
                val = el.input_value(timeout=2000)
                f["typing"] = "ok" if val == "Test" else f"Text kommt nicht an (Feld enthält «{val[:20]}»)"
                el.fill("", timeout=2000)
            except Exception as e:
                f["typing"] = "nicht möglich: " + str(e).split("\n")[0][:100]


def combo_width(page):
    return page.viewport_size["width"] if page.viewport_size else 1440


def test_accordion(page, rec):
    try:
        found = page.evaluate(ACCORDION_JS)
        if not found:
            return
        before = page.evaluate(ACC_STATE_JS)
        page.locator("[data-wc-acc]").first.click(timeout=4000)
        page.wait_for_timeout(800)
        after = page.evaluate(ACC_STATE_JS)
        opened = bool(after and before and (after["open"] or after["h"] > before["h"] + 10 or after["box"] > before["box"] + 10))
        rec["accordion"] = {**found, "opens": opened}
        page.locator("[data-wc-acc]").first.click(timeout=4000)  # close again
        page.wait_for_timeout(300)
    except Exception as e:
        rec["accordion"] = {"error": str(e).split("\n")[0][:120]}


def time_to_form(page, url, timeout_s=60):
    """3G: seconds until a form field is visible (main page or form iframe)."""
    t0 = time.time()
    page.goto(url, wait_until="commit", timeout=timeout_s * 1000)
    while time.time() - t0 < timeout_s:
        for fr in page.frames:
            try:
                if fr.locator("input:visible, textarea:visible").count():
                    if fr != page.main_frame or fr.locator("form input:visible").count():
                        return round(time.time() - t0, 1)
            except Exception:
                pass
        try:  # lazy embeds load only while in view (IntersectionObserver): go to the form and stay there
            page.evaluate(GOTO_FORM_JS, PROVIDERS)
        except Exception:
            pass  # still navigating
        page.wait_for_timeout(250)
    return None


def check_page(ctx, combo, url, wd, cookie_done, no_typing_hosts):
    page = ctx.new_page()
    rec = {"url": url, "combo": combo["id"], "engine": combo["engine"].replace("-forcedark", ""), "device": combo["device"],
           "group": combo["group"], "width": combo_width(page)}
    errors, failed = [], []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.on("requestfailed", lambda r: failed.append({"url": r.url[:200], "type": r.resource_type,
                                                      "error": (r.failure or "")[:80]}))
    page.on("response", lambda r: failed.append({"url": r.url[:200], "type": r.request.resource_type, "status": r.status})
            if r.status >= 400 else None)
    try:
        if combo["opts"].get("throttle") and combo["engine"].startswith("chromium"):
            # Chrome DevTools preset «3G»: 1.6 Mbit/s down, 750 kbit/s up, 300 ms RTT
            cdp = ctx.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 300,
                                                          "downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8})
            rec["time_to_form"] = time_to_form(page, url)
            rec["throttled"] = True
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000 if rec.get("throttled") else 45000)
        rec["status"] = resp.status if resp else None
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        host = url.split("/")[2]
        if host not in cookie_done:
            from viewports import COOKIE_JS
            try:
                rec["cookie_banner"] = page.evaluate(COOKIE_JS)
            except Exception:
                pass
            cookie_done.add(host)
            page.wait_for_timeout(600)
        scroll_through(page)
        rec.update(page.evaluate(MEASURE_JS, rec["width"]))
        rec["langswitch"] = page.evaluate(LANGSWITCH_JS, rec["width"])
        if rec["langswitch"]["found"] and not rec["langswitch"]["inside"]:
            from viewports import TOGGLE_JS
            try:
                if page.evaluate(TOGGLE_JS):
                    page.wait_for_timeout(800)
                    after = page.evaluate(LANGSWITCH_JS, rec["width"])
                    rec["langswitch"]["inside_after_menu"] = after["inside"]
                    page.keyboard.press("Escape")
                    page.reload(wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
            except Exception:
                pass
        rec["share"] = page.evaluate(SHARE_JS, rec["width"])
        test_accordion(page, rec)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        shot = os.path.join(wd, "screens", f"browser_{fid(url)}_{combo['id']}.jpg")
        page.screenshot(path=shot, type="jpeg", quality=60, timeout=30000)  # first screen only
        rec["screenshot"] = os.path.relpath(shot, wd)
        test_form(page, combo, rec, no_typing_hosts)
    except Exception as e:
        rec["error"] = str(e).split("\n")[0][:300]
    rec["js_errors"] = list(dict.fromkeys(errors))[:5]
    rec["failed_resources"] = failed[:15]
    try:
        page.close()
    except Exception:
        pass
    return rec


def summarize(wd, combos, pages, unavailable, versions):
    recs = [load_json(f) for f in glob.glob(os.path.join(wd, "browsers", "*.json"))]
    cfg = load_json(os.path.join(wd, "config.json")) or {}
    known = {c["id"]: c for c in combo_list(cfg, list(EXTRA), set())}
    done = {r["combo"] for r in recs if r["url"] in pages}
    combos = list(combos) + [known[i] for i in known if i in done and i not in {c["id"] for c in combos}]
    order = list(known)
    combos.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 99)
    ids = {c["id"] for c in combos}
    prev = load_json(os.path.join(wd, "browsers.json"), {}) or {}
    meta_prev = prev.get("meta", {})
    out = {"meta": {"combos": [{k: c[k] for k in ("id", "label", "engine", "device", "group")} for c in combos],
                    "pages": pages,
                    "unavailable": {**meta_prev.get("unavailable", {}), **unavailable},
                    "versions": {**meta_prev.get("versions", {}), **versions},
                    "date": time.strftime("%Y-%m-%d")},
           "results": sorted((r for r in recs if r["combo"] in ids and r["url"] in pages), key=lambda r: (r["url"], r["combo"]))}
    for eng in list(out["meta"]["unavailable"]):
        if any(r["engine"] == eng for r in out["results"]):
            out["meta"]["unavailable"].pop(eng)  # worked in a later run
    save_json(os.path.join(wd, "browsers.json"), out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--pages", choices=["main", "all"], default="main")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--extra", default="", help="small,dark,slow,zoom,inapp")
    ap.add_argument("--combos", default="", help="nur diese Kombinationen (ids)")
    ap.add_argument("--urls", nargs="*", default=None, help="diese Seiten statt der Auswahl aus dem Crawl")
    ap.add_argument("--time-budget", type=int, default=0)
    a = ap.parse_args()
    wd = a.workdir
    os.makedirs(os.path.join(wd, "browsers"), exist_ok=True)
    os.makedirs(os.path.join(wd, "screens"), exist_ok=True)
    cfg = load_json(os.path.join(wd, "config.json"))
    if not cfg:
        if not a.urls:
            sys.exit("config.json fehlt – detect_config.py ausführen oder --urls angeben.")
        cfg = {"languages": [{"code": "?", "start": a.urls[0], "host": a.urls[0].split("/")[2], "prefix": ""}]}
    lm = LangMap(cfg)
    extras = [x.strip() for x in a.extra.split(",") if x.strip()]
    unknown = [x for x in extras if x not in EXTRA]
    if unknown:
        sys.exit(f"Unbekannte Zusatztests: {', '.join(unknown)} (möglich: {', '.join(EXTRA)})")
    combos = combo_list(cfg, extras, {x for x in a.combos.split(",") if x})
    pages = [normalize(u) for u in a.urls] if a.urls else select_pages(wd, cfg, a.pages, a.max_pages, lm)
    form_pages = {normalize(it["test_page"]) for it in load_json(os.path.join(wd, "forms_inventory.json"), [])}
    no_typing = [it.get("frame_src", "").split("?")[0] for it in load_json(os.path.join(wd, "forms_inventory.json"), [])
                 if it.get("partial_submissions")]
    print(f"{len(pages)} Seiten × {len(combos)} Kombinationen", flush=True)
    for u in pages:
        print("  ", u, flush=True)
    find_browsers_path(wd)
    from playwright.sync_api import sync_playwright
    t0, n = time.time(), 0
    with sync_playwright() as pw:
        eng = Engines(pw, wd)
        try:
            for combo in combos:
                todo = [u for u in pages if not os.path.exists(os.path.join(wd, "browsers", f"{fid(u)}_{combo['id']}.json"))]
                if combo["group"] == "slow" and form_pages:
                    todo = [u for u in todo if u in form_pages]  # 3G only where it matters: pages with a form
                if not todo:
                    continue
                ctx = eng.context(combo)
                if ctx is None:
                    continue
                print(f"{combo['label']} ({len(todo)} Seiten)", flush=True)
                cookie_done = set()
                for u in todo:
                    if a.time_budget and time.time() - t0 > a.time_budget:
                        print("Zeitbudget erreicht – erneut starten zum Fortsetzen.", flush=True)
                        ctx.close()
                        summarize(wd, combos, pages, eng.unavailable, eng.versions)
                        return
                    rec = check_page(ctx, combo, u, wd, cookie_done, no_typing)
                    save_json(os.path.join(wd, "browsers", f"{fid(u)}_{combo['id']}.json"), rec)
                    n += 1
                    base = load_json(os.path.join(wd, "browsers", f"{fid(u)}_chrome-android.json"))
                    bad = problems(rec, base)
                    print(f"  {'✗ ' + ', '.join(bad) if bad else '✓'}  {u}", flush=True)
                ctx.close()
        finally:
            eng.close()
            out = summarize(wd, combos, pages, eng.unavailable, eng.versions)
    print(f"Browsertest abgeschlossen ({n} neu, {len(out['results'])} insgesamt) → browsers.json")
    for e_, why in out["meta"]["unavailable"].items():
        print(f"NICHT GETESTET: {ENGINE_LABEL.get(e_, e_)} – {why}")


# ---------------------------------------------------------------- shared with analyze.py / report.py
CHECKS = {  # key: (label in the report, severity if engine-specific, covered elsewhere when it fails everywhere)
    "load": ("Seite lädt nicht", "kritisch", False),
    "js": ("JavaScript-Fehler", "mittel", True),
    "overflow": ("Inhalt breiter als Bildschirm", "mittel", True),
    "images": ("Bilder werden nicht angezeigt", "mittel", True),
    "fonts": ("Webfont wird nicht geladen", "mittel", False),
    "langswitch": ("Sprachumschalter nicht erreichbar", "mittel", False),
    "form": ("Formular lädt nicht", "kritisch", False),
    "form_typing": ("Eintippen ins Formular nicht möglich", "kritisch", False),
    "form_button": ("Absende-Button abgeschnitten / ausserhalb", "kritisch", False),
    "accordion": ("Akkordeon öffnet sich nicht", "mittel", False),
    "share": ("Teilen-Buttons nicht sichtbar oder nicht antippbar", "mittel", False),
    "h1": ("Titel bricht nicht sauber um", "mittel", False),
    "clipped": ("Text abgeschnitten", "mittel", False),
    "contrast": ("Text schlecht lesbar (Kontrast)", "mittel", False),
    "slow": ("Formular erst spät bedienbar", "mittel", False),
}


def problems(rec, base=None):
    """Failed check keys of one record ({} if fine). Value = short evidence text.
    base = the light-mode record of the same page (chrome-android): dark mode only counts what is worse there."""
    p = {}
    if rec.get("error") or (rec.get("status") or 0) >= 400:
        p["load"] = rec.get("error") or f"HTTP {rec.get('status')}"
        return p
    if rec.get("js_errors"):
        p["js"] = rec["js_errors"][0][:120]
    if rec.get("scrollWidth", 0) > rec.get("width", 0) + 2:
        offs = ", ".join(f"{o['el']} bis {o['right']}px" for o in rec.get("offenders", [])[:2])
        p["overflow"] = f"Seite {rec['scrollWidth']}px bei {rec['width']}px" + (f" – {offs}" if offs else "")
    if rec.get("left_cut"):
        lc = rec["left_cut"][0]
        p["overflow"] = (p.get("overflow", "") + "; " if p.get("overflow") else "") + \
            f"links abgeschnitten: {lc['el']} beginnt bei {lc['left']}px" + (f" («{lc['text'][:40]}»)" if lc.get("text") else "")
    if rec.get("broken_images"):
        p["images"] = ", ".join(x.split("/")[-1][:40] for x in rec["broken_images"][:3])
    bad_fonts = [f["family"] for f in rec.get("fonts", []) if not f["loaded"]]
    if bad_fonts:
        p["fonts"] = ", ".join(bad_fonts)
    ls = rec.get("langswitch") or {}
    if ls.get("found") and not ls.get("inside") and not ls.get("inside_after_menu"):
        p["langswitch"] = ("ausserhalb des Bildschirms: " + "; ".join(ls["outside"])) if ls.get("outside") else "nicht sichtbar, auch nicht im Menü"
    f = rec.get("form")
    if f:
        if f.get("loaded") is False or f.get("error"):
            p["form"] = f.get("error") or f"{f.get('src') or 'Formular'}: keine Felder sichtbar"
        else:
            t = f.get("typing")
            if t and t not in ("ok",) and not t.startswith("übersprungen"):
                p["form_typing"] = f"Feld «{f.get('text_field')}»: {t}"
            if f.get("button_inside") is False or f.get("iframe_cut") or f.get("iframe_wider_than_screen"):
                p["form_button"] = ("iframe schneidet das Formular ab" if f.get("iframe_cut") else
                                    "Formular breiter als Bildschirm" if f.get("iframe_wider_than_screen") else
                                    f"Button «{f.get('button')}» ausserhalb des Rahmens")
    acc = rec.get("accordion")
    if acc and acc.get("opens") is False:
        p["accordion"] = f"«{acc.get('text') or acc.get('el')}»"
    sh = [s for s in rec.get("share", []) if not (s["visible"] and s["inside"] and s["tappable"])]
    if rec.get("share") and len(sh) == len(rec["share"]):
        p["share"] = "; ".join(f"{s['label'] or s['href'][:30]}: {'unsichtbar' if not s['visible'] else 'ausserhalb' if not s['inside'] else 'verdeckt'}" for s in sh[:3])
    if (rec.get("h1") or {}).get("overflow"):
        p["h1"] = f"«{rec['h1']['text'][:50]}»"
    if rec.get("clipped_text") and rec.get("group") in ("small", "zoom"):
        p["clipped"] = "; ".join(rec["clipped_text"][:2])
    if rec.get("group") == "dark" and rec.get("combo") != "chrome-android-forcedark" and base is not None:
        light = {x["text"] for x in base.get("low_contrast", [])}
        worse = [x for x in rec.get("low_contrast", []) if x["text"] not in light]
        if worse:
            p["contrast"] = "; ".join(f"«{x['text']}» {x['ratio']}:1" for x in worse[:3])
    if rec.get("throttled") and rec.get("form") and (rec.get("time_to_form") is None or rec["time_to_form"] > 10):
        p["slow"] = f"nach {rec['time_to_form']} s" if rec.get("time_to_form") else "nicht innert 60 s"
    return p


if __name__ == "__main__":
    main()
