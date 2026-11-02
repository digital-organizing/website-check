#!/usr/bin/env python3
"""Check that the sites to be tested are reachable from this environment.

Usage: python preflight.py https://example.ch https://exemple.ch [...]
Prints one line per host and exits 1 if any host is blocked by the sandbox proxy.
"""
import os
import sys
from urllib.parse import urlsplit

import requests

from common import UA, bare_host, launch_browser


def in_sandbox():
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")) or os.path.isdir("/mnt/user-data")


def probe(host):
    url = f"https://{host}/"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        deny = r.headers.get("x-deny-reason")
        if deny:
            return "blockiert", f"Sandbox-Proxy: {deny}"
        return "ok", f"HTTP {r.status_code} → {r.url}"
    except requests.exceptions.ProxyError as e:
        return "blockiert", f"Sandbox-Proxy verweigert Verbindung ({str(e)[:120]})"
    except requests.exceptions.SSLError as e:
        return "fehler", f"SSL-Fehler: {str(e)[:120]}"
    except requests.exceptions.ConnectionError as e:
        msg = str(e)
        if "403" in msg or "Tunnel" in msg:
            return "blockiert", "Sandbox-Proxy verweigert Verbindung"
        return "fehler", f"Keine Verbindung: {msg[:120]}"
    except requests.exceptions.RequestException as e:
        return "fehler", str(e)[:160]


def resource_hosts(start_urls):
    """Render the start pages; return (third-party hosts whose resources failed, page load errors)."""
    from playwright.sync_api import sync_playwright
    failed, errors = set(), []
    own = {bare_host(urlsplit(u if "://" in u else "https://" + u).hostname) for u in start_urls}
    with sync_playwright() as p:
        b = launch_browser(p)
        pg = b.new_page(user_agent=UA)
        pg.on("requestfailed", lambda r: failed.add(urlsplit(r.url).hostname))
        pg.on("response", lambda r: failed.add(urlsplit(r.url).hostname) if r.status == 403 else None)
        for u in start_urls:
            try:
                pg.goto(u if "://" in u else "https://" + u, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                errors.append((u, str(e).split("\n")[0][:200]))
                continue
            try:
                pg.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        b.close()
    return sorted(h for h in failed if h and bare_host(h) not in own), errors


CERT_HELP = """Der Browser vertraut dem Zertifikat des Sandbox-Proxys nicht (requests/curl schon).
  Claude Code im Web: Proxy-CA in den Browser-Zertifikatsspeicher (NSS) aufnehmen – TLS-Prüfung bleibt aktiv:
    apt-get install -y libnss3-tools   # falls certutil fehlt
    mkdir -p ~/.pki/nssdb && certutil -A -d sql:$HOME/.pki/nssdb -n sandbox-proxy-ca -t C,, -i <CA-Datei>
  CA-Datei: /root/.ccr/agent-proxy-ca.crt (bzw. die in SSL_CERT_FILE/REQUESTS_CA_BUNDLE genannte).
  Niemals ignore_https_errors oder --ignore-certificate-errors verwenden."""


def main(args):
    hosts = []
    for a in args:
        h = urlsplit(a if "://" in a else "https://" + a).hostname
        for v in (bare_host(h), "www." + bare_host(h)):
            if v not in hosts:
                hosts.append(v)
    print(f"Umgebung: {'Cloud-Sandbox (claude.ai)' if in_sandbox() else 'lokal'}")
    blocked = []
    for h in hosts:
        status, detail = probe(h)
        print(f"{status:10} {h:40} {detail}")
        if status == "blockiert":
            blocked.append(h)
    if not blocked:
        print("\nBrowser-Test und Ressourcen der Startseite(n) (CDN, Schriften, Skripte, Formular-Anbieter):")
        hosts_failed, errors = resource_hosts(args)
        for u, err in errors:
            print(f"FEHLER     {u}  {err}")
            if "CERT" in err:
                print(CERT_HELP)
        if errors:
            print("\nDer Browser kann die Seite nicht laden – erst beheben, sonst sind alle weiteren Schritte wertlos.")
            sys.exit(1)
        for h in hosts_failed if in_sandbox() else []:
            status, detail = probe(h)
            if status == "blockiert":
                blocked.append(h)
            print(f"{status:10} {h:40} {detail}")
        if not hosts_failed:
            print("ok         Seite lädt im Browser, keine blockierten Ressourcen")
    if blocked:
        print("\nBLOCKIERT – folgende Domains müssen in den Netzwerkeinstellungen erlaubt werden:")
        for h in blocked:
            print("  -", h)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
