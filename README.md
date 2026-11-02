# website-check

QA-Check für (mehrsprachige) Websites als Claude-Skill von digital/organizing: defekte Links, Formulare,
vergessene Übersetzungen, OG-Tags, Teilen-Buttons, Impressum/Datenschutz, Darstellung und
**Browser und Geräte** (Chromium, Firefox, Safari/WebKit × Desktop/Mobile). Ergebnis ist ein HTML-Bericht auf Deutsch.

## Aufbau

```
skills/website-check/        ← genau dieser Ordner ist der Skill
  SKILL.md                   ← Anleitung für Claude (Ablauf, Stopps, Fehlalarme)
  scripts/                   ← Python-Skripte, die SKILL.md aufruft
    preflight.py  detect_config.py  crawl.py  check_links.py  viewports.py
    browsers.py   forms.py  share_links.py  analyze.py  report.py  common.py
tests/                       ← Regressionstest mit lokaler Testseite (ohne Internet)
tools/build_skill.sh         ← baut dist/website-check.zip zum Hochladen
.github/workflows/ci.yml     ← Syntax + Browsertest in allen drei Engines + ZIP als Artefakt
```

Das Repo ist die **Quelle**. Der Skill bleibt in sich vollständig (SKILL.md + Skripte im selben ZIP), damit er in
claude.ai ohne zusätzliche Netzwerkfreigaben funktioniert: Claude lädt beim Check nichts von GitHub nach.

## Skill aktualisieren

**claude.ai:** `tools/build_skill.sh` ausführen (oder das ZIP aus dem letzten CI-Lauf unter «Actions → Artifacts»
herunterladen) und in claude.ai unter Einstellungen → Skills den bestehenden Skill «website-check» durch
`dist/website-check.zip` ersetzen.

**Claude Code (lokal):** den Ordner verlinken, dann ist jede Änderung im Repo sofort aktiv:

```bash
ln -s "$PWD/skills/website-check" ~/.claude/skills/website-check
```

## Entwickeln

```bash
pip install playwright requests pillow
python -m playwright install chromium firefox webkit
python tests/test_browsers.py
```

Einzelne Skripte lassen sich direkt gegen eine Website laufen lassen, z.B. ein schneller Browsertest ohne Crawl:

```bash
python skills/website-check/scripts/browsers.py /tmp/wc-test --urls https://beispiel.ch/ --extra small,dark
```

Änderungen bitte über Pull Requests: CI prüft Syntax und lässt `browsers.py` gegen die Testseite in Chromium,
Firefox und WebKit laufen.
