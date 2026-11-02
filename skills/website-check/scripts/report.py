#!/usr/bin/env python3
"""Render findings.json as a self-contained German HTML report.

Usage: python report.py WORKDIR OUTPUT.html
Screenshots referenced by findings are embedded as downscaled JPEG thumbnails.
"""
import base64
import datetime
import hashlib
import html
import io
import os
import sys
from collections import Counter, defaultdict

from common import load_json

SEV_LABEL = {"kritisch": "Kritisch", "mittel": "Mittel", "hinweis": "Hinweis"}
CATS = ["Links", "Formulare", "Teilen-Links", "Übersetzung", "Sprachversionen", "OG-Tags", "Rechtliches", "Darstellung", "Technik"]
LANG = {"de": "DE", "fr": "FR", "it": "IT", "en": "EN", "rm": "RM"}
e = html.escape


def pid(url):
    return "p" + hashlib.sha1(url.encode()).hexdigest()[:10]


def thumb(wd, rel, width=520, max_h=1400):
    path = os.path.join(wd, rel)
    if not os.path.exists(path):
        return ""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    scale = min(1.0, width / im.size[0])
    im = im.resize((int(im.size[0] * scale), int(im.size[1] * scale)))
    if im.size[1] > max_h:
        im = im.crop((0, 0, im.size[0], max_h))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def short(url):
    s = url.split("://", 1)[-1]
    return s if len(s) < 70 else s[:67] + "…"


CSS = """
:root{--ink:#1b2230;--muted:#5d6675;--line:#dfe3ea;--bg:#ffffff;--panel:#f4f6f9;
--crit:#c42b1c;--mid:#c77700;--hint:#44699c;--ok:#2d8a57;--none:#9aa3b1}
@media (prefers-color-scheme: dark){:root:not([data-theme=light]){--ink:#e7ebf2;--muted:#a3adbd;--line:#333b49;--bg:#151a22;--panel:#1d2430;
--crit:#ff6b5c;--mid:#f0a93a;--hint:#86a8dc;--ok:#5cc58b;--none:#6b7482}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "Source Sans 3","Segoe UI",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:40px 28px 80px}
h1{font-size:2.1rem;line-height:1.15;margin:0 0 8px;font-weight:700;letter-spacing:-.01em}
h2{font-size:1.45rem;margin:56px 0 14px;font-weight:700;border-top:3px solid var(--ink);padding-top:14px}
h3{font-size:1.1rem;margin:28px 0 8px}
p,li{max-width:78ch}
.lead{font-size:1.2rem;color:var(--muted);margin:0 0 24px}
.lead strong{color:var(--ink)}
a{color:inherit;text-decoration-color:var(--line);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
.warn{border-left:5px solid var(--crit);background:var(--panel);padding:14px 18px;margin:18px 0}
.warn.soft{border-color:var(--mid)}
.totals{display:flex;gap:28px;flex-wrap:wrap;margin:10px 0 0}
.totals div{font-size:.95rem;color:var(--muted)}
.totals b{display:block;font-size:2rem;line-height:1;color:var(--ink)}
.totals .k b{color:var(--crit)}.totals .m b{color:var(--mid)}.totals .h b{color:var(--hint)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap}
table.matrix td.path{font-weight:600;white-space:nowrap;max-width:340px;overflow:hidden;text-overflow:ellipsis}
table.matrix td.path small{display:block;font-weight:400;color:var(--muted)}
td.cell{min-width:150px}
.cell a.box{display:flex;gap:6px;align-items:center;text-decoration:none;padding:5px 8px;border-radius:4px;border:1px solid var(--line)}
.cell a.box:hover{border-color:var(--ink)}
.cell .missing{display:block;padding:5px 8px;border-radius:4px;color:var(--none);
 background:repeating-linear-gradient(135deg,transparent 0 6px,var(--panel) 6px 12px);border:1px dashed var(--line)}
.n{font-weight:700;font-variant-numeric:tabular-nums;min-width:1.4em;text-align:center;border-radius:3px;color:#fff;font-size:.82rem;padding:0 4px}
.n.k{background:var(--crit)}.n.m{background:var(--mid)}.n.h{background:var(--hint)}.n.ok{background:var(--ok)}
.vp{display:inline-flex;gap:3px;margin-left:auto}
.vp i{width:9px;height:9px;border-radius:50%;background:var(--ok);display:inline-block}
.vp i.bad{background:var(--mid)}.vp i.na{background:var(--none)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 18px}
.filters button{font:inherit;font-size:.9rem;border:1px solid var(--line);background:var(--bg);color:var(--ink);padding:5px 12px;border-radius:20px;cursor:pointer}
.filters button[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.filters button:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--hint);outline-offset:2px}
.finding{border-left:4px solid var(--line);padding:10px 16px;margin:0 0 12px;background:var(--panel)}
.finding.kritisch{border-color:var(--crit)}.finding.mittel{border-color:var(--mid)}.finding.hinweis{border-color:var(--hint)}
.finding .t{font-weight:600}
.finding .sev{font-size:.8rem;font-weight:700;margin-right:8px}
.finding.kritisch .sev{color:var(--crit)}.finding.mittel .sev{color:var(--mid)}.finding.hinweis .sev{color:var(--hint)}
.finding .d{color:var(--muted);word-break:break-all;font-size:.93rem}
.finding ul{margin:6px 0 0;padding-left:20px;font-size:.9rem}
.finding li{word-break:break-word}
.finding .comment{margin-top:8px;font-style:italic}
details>summary{cursor:pointer;color:var(--muted);font-size:.9rem;margin-top:6px}
.shots{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.shots img{max-width:260px;border:1px solid var(--line)}
.tag{display:inline-block;font-size:.78rem;border:1px solid var(--line);border-radius:3px;padding:0 5px;margin-right:4px;color:var(--muted)}
.okc{color:var(--ok);font-weight:600}.badc{color:var(--crit);font-weight:600}.midc{color:var(--mid);font-weight:600}
.pagebox{border-top:1px solid var(--line);padding:10px 0}
.pagebox summary{color:var(--ink);font-weight:600}
footer{margin-top:60px;color:var(--muted);font-size:.9rem}
@media (max-width:640px){.wrap{padding:24px 16px}h1{font-size:1.6rem}}
@media print{.filters{display:none}details{display:block}h2{break-before:page}}
"""

JS = """
const btns=[...document.querySelectorAll('.filters button')];
function apply(){const sev=btns.filter(b=>b.dataset.sev&&b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.sev);
 const lang=btns.filter(b=>b.dataset.lang&&b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.lang);
 document.querySelectorAll('#befunde .finding').forEach(f=>{const ok=sev.includes(f.dataset.sev)&&(!lang.length||lang.includes(f.dataset.lang)||!f.dataset.lang);
 f.hidden=!ok});
 document.querySelectorAll('#befunde .catblock').forEach(c=>{c.hidden=![...c.querySelectorAll('.finding')].some(f=>!f.hidden)});}
btns.forEach(b=>b.addEventListener('click',()=>{b.setAttribute('aria-pressed',b.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
apply();
"""


def finding_html(f, wd, embed=True):
    parts = [f'<div class="finding {f["sev"]}" data-sev="{f["sev"]}" data-lang="{f.get("lang") or ""}" id="f{f["id"]}">',
             f'<div><span class="sev">{SEV_LABEL[f["sev"]]}</span>'
             f'{"<span class=tag>" + LANG.get(f["lang"], f["lang"]) + "</span>" if f.get("lang") else ""}'
             f'{"<span class=tag>manuell</span>" if f.get("manual") else ""}'
             f'<span class="t">{e(f["title"])}</span></div>']
    if f.get("detail"):
        parts.append(f'<div class="d">{e(f["detail"])}</div>')
    if f.get("evidence"):
        ev = f["evidence"]
        parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in ev[:8]) + "</ul>")
        if len(ev) > 8:
            parts.append("<details><summary>" + f"{len(ev) - 8} weitere</summary><ul>" +
                         "".join(f"<li>{e(x)}</li>" for x in ev[8:]) + "</ul></details>")
    pages = f.get("pages", [])
    if pages:
        label = f"Betrifft {len(pages)} Seite{'n' if len(pages) != 1 else ''}"
        inner = "".join(f'<li><a href="{e(u)}" target="_blank" rel="noopener">{e(short(u))}</a></li>' for u in pages[:60])
        if len(pages) == 1 and not f.get("evidence"):
            parts.append(f'<div class="d"><a href="{e(pages[0])}" target="_blank" rel="noopener">{e(short(pages[0]))}</a></div>')
        else:
            parts.append(f"<details><summary>{label}</summary><ul>{inner}</ul></details>")
    if embed and f.get("screenshots"):
        imgs = "".join(f'<a href="{src}" target="_blank"><img src="{src}" alt="Screenshot" loading="lazy"></a>'
                       for src in (thumb(wd, s) for s in f["screenshots"][:3]) if src)
        if imgs:
            parts.append(f'<details><summary>Screenshots</summary><div class="shots">{imgs}</div></details>')
    if f.get("comment"):
        parts.append(f'<div class="comment">Anmerkung: {e(f["comment"])}</div>')
    parts.append("</div>")
    return "".join(parts)


def main(wd, out_path):
    d = load_json(os.path.join(wd, "findings.json"))
    F = d["findings"]
    langs = d["languages"]
    c = Counter(f["sev"] for f in F)
    by_page = defaultdict(list)
    for f in F:
        for u in f.get("pages", []):
            by_page[u].append(f)
    today = datetime.date.today().strftime("%d.%m.%Y")
    H = []
    H.append(f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Website-Check {e(d['site'])} – {today}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">""")
    H.append(f"<h1>Website-Check {e(d['site'])}</h1>")
    lead = (f"<strong>{d['pages_html']} Seiten</strong> in <strong>{len(langs)} Sprache{'n' if len(langs) > 1 else ''}</strong> "
            f"({', '.join(LANG.get(l, l) + ': ' + str(d['pages_per_lang'].get(l, 0)) for l in langs)}) geprüft, "
            f"je in {len(d['viewports'])} Bildschirmgrössen. Stand {today}.")
    H.append(f'<p class="lead">{lead}</p>')
    H.append(f'<div class="totals"><div class="k"><b>{c["kritisch"]}</b>kritisch</div><div class="m"><b>{c["mittel"]}</b>mittel</div>'
             f'<div class="h"><b>{c["hinweis"]}</b>Hinweise</div></div>')
    if d.get("render_degraded"):
        H.append('<div class="warn"><strong>Darstellung nicht aussagekräftig.</strong> Beim Test waren Ressourcen-Domains '
                 f'blockiert ({e(", ".join(d["blocked_resource_hosts"]))}). Die Seiten wurden ohne CSS/JavaScript geladen, '
                 'deshalb fehlen die Prüfungen zu Bildschirmgrössen und Formular-Ergebnisse können verfälscht sein. '
                 'Diese Domains in den Netzwerkeinstellungen erlauben und den Check wiederholen, oder lokal ausführen.</div>')
    if d.get("crawl_truncated") or not d.get("crawl_finished"):
        H.append(f'<div class="warn soft"><strong>Crawl unvollständig.</strong> {d.get("crawl_truncated") or "Einige"} '
                 'gefundene Seiten wurden wegen des Seitenlimits nicht geprüft.</div>')

    # ------------------------------------------------ matrix
    vp_names = [v.split(" ")[0] for v in d["viewports"]]
    H.append("<h2 id='matrix'>Seiten × Sprachen</h2>")
    H.append("<p>Jede Zeile ist eine Seite mit ihren Sprachversionen. Zahlen = Befunde je Schweregrad; "
             "Punkte = Bildschirmgrössen (" + ", ".join(e(v) for v in d["viewports"]) + "). Schraffiert = Sprachversion fehlt.</p>")
    H.append('<div class="scroll"><table class="matrix"><thead><tr><th>Seite</th>' +
             "".join(f"<th>{LANG.get(l, l)}</th>" for l in langs) + "</tr></thead><tbody>")
    for g in d["groups"]:
        H.append(f'<tr><td class="path" title="{e(g["path"])}">{e(g["path"])}<small>{e(g["title"])}</small></td>')
        for l in langs:
            u = g["pages"].get(l)
            if not u:
                H.append('<td class="cell"><span class="missing">fehlt</span></td>')
                continue
            cc = Counter(f["sev"] for f in by_page.get(u, []))
            nums = "".join(f'<span class="n {k[0]}">{cc[k]}</span>' for k in ("kritisch", "mittel", "hinweis") if cc[k]) \
                or '<span class="n ok">✓</span>'
            vps = d["viewport_summary"].get(u, {})
            dots = "".join(
                f'<i class="{"na" if (d.get("render_degraded") or v not in vps) else ("bad" if vps[v]["issues"] else "")}" '
                f'title="{e(v)}: {e(", ".join(vps.get(v, {}).get("issues", [])) or ("nicht geprüft" if v not in vps or d.get("render_degraded") else "ok"))}"></i>'
                for v in vp_names)
            H.append(f'<td class="cell"><a class="box" href="#{pid(u)}">{nums}<span class="vp">{dots}</span></a></td>')
        H.append("</tr>")
    H.append("</tbody></table></div>")

    # ------------------------------------------------ findings by category
    H.append("<h2 id='befunde'>Befunde</h2><div id='befunde'>")
    H.append('<div class="filters">' +
             "".join(f'<button type="button" data-sev="{s}" aria-pressed="true">{SEV_LABEL[s]} ({c[s]})</button>'
                     for s in ("kritisch", "mittel", "hinweis")) +
             ("".join(f'<button type="button" data-lang="{l}" aria-pressed="false">nur {LANG.get(l, l)}</button>' for l in langs)
              if len(langs) > 1 else "") + "</div>")
    cats = CATS + sorted({f["cat"] for f in F} - set(CATS))
    for cat in cats:
        items = [f for f in F if f["cat"] == cat]
        if not items:
            continue
        H.append(f'<div class="catblock"><h3>{e(cat)} <span class="tag">{len(items)}</span></h3>')
        H.extend(finding_html(f, wd) for f in items)
        H.append("</div>")
    if not F:
        H.append("<p class='okc'>Keine Befunde.</p>")
    H.append("</div>")

    # ------------------------------------------------ legal
    H.append("<h2 id='rechtliches'>Impressum und Datenschutz</h2>")
    H.append('<div class="scroll"><table><thead><tr><th>Sprache</th><th>Seite</th><th>Gefunden</th><th>Verlinkt auf</th></tr></thead><tbody>')
    for kind, per_lang in d["legal"].items():
        for l in langs:
            info = per_lang.get(l, {})
            url = info.get("url")
            linked = f'{info.get("linked_from", 0)} von {info.get("pages_in_lang", 0)} Seiten'
            full = info.get("pages_in_lang") and info.get("linked_from", 0) >= 0.9 * info["pages_in_lang"]
            H.append(f"<tr><td>{LANG.get(l, l)}</td><td>{e(kind)}</td>"
                     f"<td>{'<a href=' + chr(34) + e(url) + chr(34) + ' target=_blank>' + e(short(url)) + '</a>' if url else '<span class=badc>nicht gefunden</span>'}</td>"
                     f"<td class='{'okc' if full else 'midc'}'>{linked if url else '–'}</td></tr>")
    H.append("</tbody></table></div>")

    # ------------------------------------------------ forms
    H.append("<h2 id='formulare'>Formulare</h2>")
    forms = d.get("forms", [])
    if not forms:
        H.append("<p>Keine Formulare gefunden.</p>")
    else:
        H.append("<p>Testeinträge wurden mit hallo@digitalorganizing.ch und dem Namen «Test d/o Website-Check» abgeschickt "
                 "und können beim Kunden gelöscht werden.</p>")
        H.append('<div class="scroll"><table><thead><tr><th>Sprache</th><th>Typ</th><th>Testseite</th><th>Vorkommen</th><th>Ergebnis</th></tr></thead><tbody>')
        for it in forms:
            r = it.get("result") or {}
            if not it["submit"]:
                res = f"<span class='midc'>nicht abgeschickt</span><br><small>{e(it.get('note', ''))}</small>"
            elif not r:
                res = "<span class='midc'>nicht getestet</span>"
            else:
                cls = {"erfolgreich": "okc", "fehlgeschlagen": "badc"}.get(r["result"], "midc")
                res = f"<span class='{cls}'>{e(r['result'])}</span><br><small>{e(r.get('detail', ''))}</small>"
            H.append(f"<tr><td>{LANG.get(it['lang'], it['lang'] or '–')}</td><td>{e(it['type'])}{' <span class=tag>Captcha</span>' if it.get('captcha') else ''}</td>"
                     f"<td><a href='{e(it['test_page'])}' target=_blank>{e(short(it['test_page']))}</a></td>"
                     f"<td>{len(it['pages'])} Seite{'n' if len(it['pages']) != 1 else ''}</td><td>{res}</td></tr>")
        H.append("</tbody></table></div>")

    # ------------------------------------------------ share links
    share = d.get("share")
    if share and share.get("links"):
        tmap = {t["url"]: t for t in share["targets"]}
        H.append("<h2 id='teilen'>Teilen-Links</h2>")
        H.append("<p>Jeder Teilen-Button wurde entschlüsselt: vorausgefüllter Text, geteilte Adresse, wohin sie im Browser "
                 "führt und welche Vorschau Facebook, LinkedIn, WhatsApp &amp; Co. anzeigen. Diese Dienste führen kein "
                 "JavaScript aus – die Vorschau stammt deshalb aus dem HTML der geteilten Adresse selbst.</p>")
        H.append('<div class="scroll"><table><thead><tr><th>Spr.</th><th>Kanal</th><th>Vorausgefüllter Text</th>'
                 '<th>Geteilte Adresse → Ziel</th><th>Vorschau</th><th>Ergebnis</th></tr></thead><tbody>')
        for rec in share["links"]:
            P = rec["params"]
            txt = " | ".join(f"{k}: {v}" for k, v in P.items() if k in ("text", "subject", "body", "title", "summary", "quote", "description") and v)
            tg = []
            for s_url in rec["shared"]:
                t = tmap.get(s_url, {})
                fin = t.get("final", "")
                trk = f" <span class=tag>{e(t['tracking'])}</span>" if t.get("tracking") else ""
                tg.append(f"{e(short(s_url))}<br>→ {e(short(fin)) if fin else '<span class=badc>' + e(t.get('error', '–')) + '</span>'}{trk}")
            pv_cells = []
            for s_url in rec["shared"]:
                pv = (tmap.get(s_url) or {}).get("preview") or {}
                m = pv.get("meta", {})
                img = (tmap.get(s_url) or {}).get("preview_image") or {}
                title = m.get("og:title") or ""
                pv_cells.append((f"<b>{e(title[:70])}</b>" if title else f"<span class=midc>kein og:title</span> ({e(pv.get('title', '')[:50])})") +
                                (f"<br><small>{e(m.get('og:description', '')[:110])}</small>" if m.get("og:description") else "") +
                                (f"<br><small>Bild {img['w']}×{img['h']}</small>" if img.get("w") else ""))
            probs = rec["problems"] + [p for s_url in rec["shared"] for p in (tmap.get(s_url) or {}).get("problems", [])]
            probs = sorted(probs, key=lambda p: {"kritisch": 0, "mittel": 1, "hinweis": 2}[p[0]])
            probs = sorted(probs, key=lambda p: {"kritisch": 0, "mittel": 1, "hinweis": 2}[p[0]])
            worst = min((p[0] for p in probs), key=lambda x: {"kritisch": 0, "mittel": 1, "hinweis": 2}[x], default=None)
            cls = {"kritisch": "badc", "mittel": "midc", "hinweis": ""}.get(worst, "okc")
            res = f"<span class='{cls}'>{'ok' if not probs else str(len(probs)) + ' Befund' + ('e' if len(probs) > 1 else '')}</span>"
            if probs:
                res += "<br><small>" + "<br>".join(e(p[1]) for p in probs[:4]) + ("<br>…" if len(probs) > 4 else "") + "</small>"
            H.append(f"<tr><td>{LANG.get(rec['lang'], rec['lang'] or '–')}</td><td>{e(rec['platform'])}</td>"
                     f"<td><small>{e(txt[:260]) or '–'}</small></td><td><small>{'<br>'.join(tg) or '–'}</small></td>"
                     f"<td><small>{'<hr>'.join(pv_cells) or '–'}</small></td><td>{res}</td></tr>")
        H.append("</tbody></table></div>")

    # ------------------------------------------------ OG table
    H.append("<h2 id='og'>OG-Tags pro Seite</h2>")
    H.append('<details><summary>Tabelle mit allen OG-Werten anzeigen</summary><div class="scroll"><table><thead><tr>'
             '<th>Seite</th><th>Spr.</th><th>og:title</th><th>og:description</th><th>og:image</th><th>og:locale</th><th>Probleme</th></tr></thead><tbody>')
    for row in d["og_table"]:
        img = row.get("img") or {}
        imgtxt = f'{img["w"]}×{img["h"]}' if img.get("w") else ("blockiert" if img.get("blocked") else (img.get("error") or ("–" if not row.get("og:image") else "")))
        H.append(f"<tr><td><a href='{e(row['url'])}' target=_blank>{e(short(row['url']))}</a></td><td>{LANG.get(row['lang'], row['lang'] or '')}</td>"
                 f"<td>{e(row.get('og:title', '')[:80]) or '<span class=badc>fehlt</span>'}</td>"
                 f"<td>{e(row.get('og:description', '')[:120]) or '<span class=badc>fehlt</span>'}</td>"
                 f"<td>{e(imgtxt)}</td><td>{e(row.get('og:locale', '')) or '–'}</td>"
                 f"<td>{'<span class=okc>ok</span>' if not row['problems'] else e('; '.join(row['problems']))}</td></tr>")
    H.append("</tbody></table></div></details>")

    # ------------------------------------------------ per page
    H.append("<h2 id='seiten'>Seiten im Detail</h2>")
    for g in d["groups"]:
        for l in langs:
            u = g["pages"].get(l)
            if not u:
                continue
            fs = by_page.get(u, [])
            cc = Counter(f["sev"] for f in fs)
            nums = " ".join(f'<span class="n {k[0]}">{cc[k]}</span>' for k in ("kritisch", "mittel", "hinweis") if cc[k]) or '<span class="n ok">✓</span>'
            H.append(f'<details class="pagebox" id="{pid(u)}"><summary>{nums} {LANG.get(l, l)} {e(short(u))}</summary>')
            H.append(f'<p><a href="{e(u)}" target="_blank" rel="noopener">Seite öffnen</a></p>')
            H.extend(finding_html(f, wd, embed=False) for f in fs)
            if not fs:
                H.append("<p class='okc'>Keine Befunde auf dieser Seite.</p>")
            H.append("</details>")

    # ------------------------------------------------ unverifiable + method
    unv = d.get("unverifiable", {})
    if unv:
        H.append("<h2 id='nicht-pruefbar'>Nicht prüfbar</h2><p>Diese Ziele konnten technisch nicht geprüft werden und sollten "
                 "stichprobenartig von Hand kontrolliert werden.</p>")
        for reason, urls in unv.items():
            H.append(f"<details><summary>{e(reason)} ({len(urls)})</summary><ul>" +
                     "".join(f"<li>{e(short(x))}</li>" for x in urls[:200]) + "</ul></details>")
    H.append("<h2 id='methode'>Vorgehen</h2><p>Alle internen Links wurden rekursiv verfolgt (inkl. versteckter Menüs, Footer und "
             f"Sitemap), maximale Linktiefe {d['max_depth']}. Jede Seite wurde in jeder konfigurierten Sprache geprüft "
             f"({', '.join(LANG.get(l, l) for l in langs)}; Ausgangssprache {LANG.get(d['source_language'], d['source_language'])}) "
             f"und in den Bildschirmgrössen {', '.join(e(v) for v in d['viewports'])} gerendert. "
             "Übersetzungsbefunde beruhen auf einer automatischen Spracherkennung und einem Vergleich mit der Ausgangssprache; "
             "einzelne Eigennamen oder Fachbegriffe können als Fehlalarm erscheinen.</p>")
    if d.get("suppressed"):
        H.append(f"<p>{d['suppressed']} automatische Befunde wurden nach manueller Durchsicht als Fehlalarm entfernt.</p>")
    H.append(f"<footer>Website-Check von digital/organizing, erstellt am {today}</footer></div><script>{JS}</script></body></html>")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(H))
    print(f"Bericht → {out_path} ({os.path.getsize(out_path) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
