#!/usr/bin/env python3
"""Check every link target found during the crawl (internal files + external links).

Usage: python check_links.py WORKDIR
Writes WORKDIR/links.json: {target_url: {result, status, detail, final}}
result ∈ ok | umleitung | kaputt | fehler | blockiert (Sandbox) | botschutz | ungeprueft
Internal HTML pages already visited by the crawler reuse the crawler's status.
"""
import glob
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, unquote

import requests

from common import UA, LangMap, load_json, save_json, normalize, bare_host
from share_links import is_share

BOT_HOSTS = ("linkedin.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "tiktok.com")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def check(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8"},
                         timeout=20, allow_redirects=True, stream=True)
        r.close()
        if r.headers.get("x-deny-reason"):
            return {"result": "blockiert", "status": None, "detail": "Domain in der Sandbox nicht erlaubt"}
        host = bare_host(urlsplit(url).hostname)
        if r.status_code in (999, 429) or (r.status_code in (400, 401, 403) and host.endswith(BOT_HOSTS)):
            return {"result": "botschutz", "status": r.status_code, "detail": "Bot-Schutz – manuell prüfen"}
        if r.status_code >= 400:
            return {"result": "kaputt", "status": r.status_code, "detail": f"HTTP {r.status_code}"}
        final = normalize(r.url)
        if final != normalize(url):
            return {"result": "umleitung", "status": r.status_code, "final": final, "detail": f"→ {final}"}
        return {"result": "ok", "status": r.status_code}
    except requests.exceptions.ProxyError:
        return {"result": "blockiert", "status": None, "detail": "Domain in der Sandbox nicht erlaubt"}
    except requests.exceptions.ConnectionError as e:
        if "403" in str(e) or "Tunnel" in str(e):
            return {"result": "blockiert", "status": None, "detail": "Domain in der Sandbox nicht erlaubt"}
        return {"result": "kaputt", "status": None, "detail": "Keine Verbindung / Domain existiert nicht"}
    except requests.exceptions.Timeout:
        return {"result": "fehler", "status": None, "detail": "Timeout (20s)"}
    except requests.exceptions.RequestException as e:
        return {"result": "fehler", "status": None, "detail": str(e)[:150]}


def main(wd):
    cfg = load_json(os.path.join(wd, "config.json"))
    lm = LangMap(cfg)
    pages = {p["url"]: p for p in (load_json(f) for f in glob.glob(os.path.join(wd, "pages", "*.json")))}
    results, to_fetch = {}, set()
    for p in pages.values():
        for ln in p.get("links", []):
            h, raw = ln["href"], (ln.get("raw") or "").strip()
            if is_share(h, raw):
                continue  # share buttons are checked by share_links.py (a status code says nothing there)
            if raw.lower().startswith("mailto:"):
                addr = raw[7:].split("?")[0]
                results[raw] = {"result": "ok" if all(EMAIL.match(x.strip()) for x in addr.split(",") if x.strip())
                                else "kaputt", "detail": "E-Mail-Adresse ungültig" if not EMAIL.match(addr) else ""}
            elif raw.lower().startswith("tel:"):
                num = re.sub(r"[\s\-/().]", "", unquote(raw[4:]))  # tel:+41%2044%20… works on most devices
                valid = bool(re.fullmatch(r"\+?\d{6,15}", num))
                results[raw] = {"result": "ok" if valid else "kaputt", "detail": "" if valid else "Telefonnummer ungültig"}
                if valid and "%" in raw:
                    results[raw]["note"] = "Telefonnummer mit kodierten Leerzeichen (%20) – funktioniert meist, besser ohne Leerzeichen"
            elif h.startswith("http"):
                n = normalize(h)
                if n in pages and pages[n].get("status") is not None:
                    st = pages[n]["status"]
                    results[n] = {"result": "kaputt" if st >= 400 else "ok", "status": st,
                                  "detail": f"HTTP {st}" if st >= 400 else ""}
                    fin = pages[n].get("final_url")
                    if st < 400 and fin and fin != n:
                        results[n].update(result="umleitung", final=fin, detail=f"→ {fin}")
                elif n in pages:
                    results[n] = {"result": "fehler", "detail": pages[n].get("error", "Ladefehler")}
                else:
                    to_fetch.add(n)
    print(f"{len(to_fetch)} Link-Ziele werden geprüft …", flush=True)
    with ThreadPoolExecutor(12) as ex:
        for u, r in zip(sorted(to_fetch), ex.map(check, sorted(to_fetch))):
            results[u] = r
    save_json(os.path.join(wd, "links.json"), results)
    from collections import Counter
    print(Counter(r["result"] for r in results.values()))


if __name__ == "__main__":
    main(sys.argv[1])
