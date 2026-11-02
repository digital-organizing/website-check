#!/usr/bin/env python3
"""Regression test for browsers.py against a local fixture page (no internet needed).

    python tests/test_browsers.py            # all engines that are installed
Checks: the left-cut text is found, form typing works, the form is never submitted, accordion opens,
share button is tappable, engines that are missing are reported instead of crashing.
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(ROOT, "..", "skills", "website-check", "scripts")
sys.path.insert(0, S)


def main():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.join(ROOT, "fixture"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    wd = tempfile.mkdtemp(prefix="wc-test-")
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    r = subprocess.run([sys.executable, os.path.join(S, "browsers.py"), wd, "--urls", url, "--extra", "small"],
                       capture_output=True, text=True, env=env, timeout=900)
    print(r.stdout[-3000:], r.stderr[-3000:])
    assert r.returncode == 0, "browsers.py failed"
    from browsers import problems
    data = json.load(open(os.path.join(wd, "browsers.json")))
    recs = {x["combo"]: x for x in data["results"]}
    assert "chrome-desktop" in recs and "chrome-android" in recs, "Chromium must always run"
    for cid, rec in recs.items():
        p = problems(rec)
        assert "load" not in p, (cid, p)
        assert rec["form"]["typing"] == "ok", (cid, rec["form"])
        assert rec.get("accordion", {}).get("opens"), (cid, rec.get("accordion"))
        assert rec["share"] and rec["share"][0]["tappable"], (cid, rec["share"])
        assert rec["langswitch"]["inside"] >= 1, (cid, rec["langswitch"])
        if rec["width"] < 340:
            assert "overflow" in p and "links abgeschnitten" in p["overflow"], (cid, p)
        if rec["width"] >= 1000:
            assert "overflow" not in p, (cid, p)
    print("Getestet:", ", ".join(sorted(recs)), "| nicht verfügbar:", data["meta"]["unavailable"] or "–")
    print("OK")
    srv.shutdown()


if __name__ == "__main__":
    main()
