---
name: website-check
description: Gründlicher QA-Check einer (mehrsprachigen) Website mit HTML-Bericht auf Deutsch – defekte Links, nicht funktionierende Formulare (echte Testeinträge), vergessene Übersetzungen (meist DE→FR), OG-Tags pro Sprache, Teilen-Buttons (WhatsApp, Telegram, Facebook, LinkedIn, E-Mail … inkl. Link-Vorschau und Danke-Seiten), Impressum/Datenschutz und Darstellung auf Mobile/Tablet/Desktop, als Matrix Seite × Sprache. Verwende diesen Skill immer, wenn jemand eine Website prüfen, testen, abnehmen oder vor dem Go-live kontrollieren will – auch bei Formulierungen wie «schau mal ob auf der Seite alles funktioniert», «Website-Abnahme», «Link-Check», «sind alle Übersetzungen drin», «check die FR-Version», «funktionieren die Formulare», «OG-Tags prüfen», «funktionieren die Teilen-Buttons», «Share-Links prüfen», «QA vor Launch» oder wenn nur eine URL mit der Bitte um Kontrolle kommt.
---

# Website-Check

Prüft eine Website so, wie es eine sorgfältige Person vor dem Go-live tun würde – nur vollständig: jede
erreichbare Seite, in jeder Sprache, in drei Bildschirmgrössen. Ergebnis ist ein HTML-Bericht auf Deutsch.

Was typischerweise vergessen geht und deshalb besonders zählt: **Impressum und Datenschutzerklärung** (in jeder
Sprache vorhanden, übersetzt, überall verlinkt), **tiefe Seiten** (nicht nur die Hauptnavigation), **FR-Versionen
mit deutschen Resten** (Buttons, Footer, Formularmeldungen, OG-Texte), **Formulare, die zwar aussehen, als
würden sie funktionieren**, und **Danke-Seiten mit Teilen-Buttons** – die sind nur nach dem Absenden erreichbar,
also für jeden Crawler unsichtbar, und genau dort steckt oft der Fehler (leerer Telegram-Link, Vorschau ohne
Titel, FR-Teilen-Text auf Deutsch).

## Ablauf auf einen Blick

```
0. Umgebung prüfen  → in der Cloud: Domains freischalten lassen (STOPP bis grün)
1. Sprachen erkennen → Vorschlag dem User bestätigen lassen (STOPP)
2. Crawl            → alle Seiten, alle Sprachen (Hintergrund)
3. Links + Darstellung
4. Formulare        → Liste zeigen, OK abholen (STOPP), dann echt abschicken
   → Danke-Seiten nachcrawlen
4b. Teilen-Links    → Text, Ziel, Link-Vorschau
5. Analyse + manuelle Durchsicht der Befunde
6. HTML-Bericht     → an User übergeben
```

Die Skripte liegen in `scripts/` neben dieser Datei. Setze `S=<pfad-zu-diesem-skill>/scripts` und arbeite in
einem eigenen Arbeitsordner pro Website, z.B. `WD=/home/claude/wc-<domain>` (lokal: `./wc-<domain>`).
Alle Skripte sind wiederaufnehmbar – bei Abbruch einfach neu starten, bereits Geprüftes wird übersprungen.

Voraussetzungen: Python 3 mit `playwright` (Chromium), `requests`, `Pillow`. Lokal ggf. einmalig
`pip install playwright requests pillow && playwright install chromium`. Ist schon ein Chromium vorinstalliert
(z.B. Claude Code im Web unter `/opt/pw-browsers`), **nicht** `playwright install` ausführen: Die Skripte nehmen
bei Versionskonflikt automatisch das vorhandene Chromium (`CHROMIUM_PATH` überschreibt die Suche).

## 0. Umgebung prüfen – immer zuerst

Frage nach der/den Start-URL(s), falls nicht angegeben. Bei Sprachversionen auf **eigenen Domains**
(z.B. `beispiel.ch` und `exemple.ch`) alle Domains angeben.

**In der Cloud (claude.ai)** ist das Netzwerk auf freigegebene Domains beschränkt. Bevor irgendetwas anderes
passiert, den User darauf hinweisen – in der ersten Antwort, noch vor dem ersten Test:

> Damit ich die Website prüfen kann, müssen ihre Domains in den Netzwerkeinstellungen erlaubt sein
> (Einstellungen → Fähigkeiten/Code-Ausführung → erlaubte Domains): `beispiel.ch`, `www.beispiel.ch`, …
> Ich prüfe gleich, welche zusätzlich nötig sind (CDN, Schriften, Formular-Anbieter).

Dann:

```bash
python $S/preflight.py https://beispiel.ch https://exemple.ch
```

Das Skript prüft die Domains selbst **und** rendert die Startseite, um blockierte Ressourcen-Domains zu finden
(CDN, Webfonts, Formular-iframes wie Tally/HubSpot). Das ist wichtig: Sind CSS/JS blockiert, lädt die Seite
ungestylt – Darstellungsprüfungen wären wertlos und Formulare funktionieren oft nicht. Exit-Code 1 = blockiert.

- Blockiert → dem User die vollständige Liste geben und **warten**. Hinweis mitgeben: Änderungen an den
  Netzwerkeinstellungen greifen meist erst in einem **neuen Chat** – falls die Freigabe erfolgt ist, aber die
  Preflight-Prüfung weiter blockiert meldet, einen neuen Chat vorschlagen.
- Links auf **externe** Seiten (Presse, Partner, Social Media) werden ebenfalls geprüft, sind in der Cloud aber
  meistens blockiert. Die landen im Bericht unter «Nicht prüfbar» – das ist in Ordnung und kein Grund zu warten.
  Wer sie mitprüfen will, führt den Skill lokal aus (Claude Code / Cowork).
- Wenn der User ausdrücklich trotzdem weitermachen will, geht das – der Bericht markiert die Darstellung dann
  automatisch als «nicht aussagekräftig».

**Lokal** gibt es diese Einschränkung nicht; preflight kurz laufen lassen reicht.

preflight lädt die Startseiten auch im Browser. Bricht es mit `ERR_CERT_AUTHORITY_INVALID` ab (Claude Code im Web:
der Browser kennt die CA des Sandbox-Proxys nicht), die ausgegebene Anleitung befolgen (Proxy-CA mit `certutil` in
`~/.pki/nssdb` aufnehmen) und preflight erneut starten. Nie die Zertifikatsprüfung abschalten.

## 1. Sprachen erkennen und bestätigen

```bash
mkdir -p $WD && python $S/detect_config.py --out $WD/config.json https://beispiel.ch [https://exemple.ch]
```

Liest `<html lang>`, hreflang-Tags und den Sprachumschalter und schlägt pro Sprache Start-URL, Host und
Pfad-Präfix vor. Häufige Muster:

| Muster | config |
|---|---|
| eigene Domains | `{"code":"fr","host":"exemple.ch","prefix":""}` |
| /de/ und /fr/ | `prefix: "/de/"` bzw. `"/fr/"` |
| DE ohne Präfix, FR unter /fr/ | DE `prefix: ""`, FR `prefix: "/fr/"` (längstes Präfix gewinnt) |

Achtung bei **eigenen Domains pro Sprache**: Viele Systeme (z.B. Webflow) setzen hreflang und Sprachumschalter
auf die *eigene* Domain (`beispiel.ch/fr`), die dann erst auf `exemple.ch/fr` weiterleitet. `detect_config.py`
schlägt dann für beide Sprachen denselben Host vor – mit den vom User genannten Domains korrigieren
(`"host": "exemple.ch", "start": "https://www.exemple.ch/fr"`). Die Weiterleitung selbst ist ein Befund.

**Den Vorschlag dem User zeigen und bestätigen lassen**, bevor gecrawlt wird – falsch erkannte Sprachen
verfälschen den ganzen Bericht. Bei Unklarheiten (z.B. Hinweis «mehrere Sprachen ohne Präfix», nur eine
Sprache erkannt, obwohl es eine FR-Version geben müsste) nachfragen statt raten. `config.json` danach bei Bedarf
direkt anpassen. Weitere Felder: `source_language` (Ausgangssprache, Default `de`), `max_pages` (Default 800),
`viewports`, `extra_internal_hosts` (z.B. `shop.beispiel.ch`, falls mitgeprüft werden soll), `form_test`
(Testdaten, Default hallo@digitalorganizing.ch / «Test d/o Website-Check»).

## 2. Crawl

```bash
cd $WD && nohup python $S/crawl.py . > crawl.out 2>&1 &
tail -3 crawl.log   # regelmässig pollen
```

Startet bei jeder Sprache und bei allen URLs aus sitemap.xml und folgt **jedem** internen Link im DOM – auch
versteckten in Mobile-Menüs, Accordions und Footern. Dadurch werden tiefe Seiten erreicht, die kein Mensch
durchklicken würde. Pro Seite werden Status, Links, Formulare, Meta/OG-Tags, hreflang und alle Texte gespeichert.

Grosse Websites laufen im Hintergrund; zwischendurch dem User kurz den Fortschritt melden. Falls
`max_pages` erreicht wird, dem User sagen, wie viele Seiten noch in der Queue sind, und fragen, ob das Limit
erhöht werden soll (`config.json` → `max_pages`, dann `crawl.py` erneut starten – es macht weiter).

## 3. Links und Darstellung

```bash
python $S/check_links.py .
nohup python $S/viewports.py . > vp.out 2>&1 &    # rendert jede Seite in 375/768/1440 px
```

`check_links.py` prüft jedes Linkziel (intern und extern, mailto/tel, Sprungmarken) und unterscheidet
«kaputt» von «nicht prüfbar» (Sandbox-Sperre, Bot-Schutz bei LinkedIn/Instagram).

`viewports.py` misst pro Seite × Bildschirmgrösse horizontales Überlaufen (mit den verursachenden Elementen),
ob die Navigation erreichbar ist (öffnet Burger-Menüs selbst), kaputte Bilder, und macht Screenshots. Das ist
der langsamste Schritt (Seiten × 3) – im Hintergrund laufen lassen.

## 4. Formulare – erst zeigen, dann echt abschicken

```bash
python $S/forms.py . inventory
```

Bei eingebetteten Formularen (Tally, HubSpot …) liest das Inventar zusätzlich die Formular-Konfiguration und
zeigt interne Weiterleitungsziele an («leitet evtl. weiter auf …/danke»). Diese Danke-Seiten kommen automatisch in
die Crawl-Queue – auch wenn das Formular später nicht abgeschickt wird.

Das Inventar fasst gleiche Formulare zusammen (ein Newsletter-Formular im Footer von 200 Seiten wird einmal
pro Sprache getestet). Login- und Zahlungs-/Spendenformulare stehen auf «NICHT abschicken» – dort werden
niemals Zahlungsdaten eingegeben.

**Die Liste dem User zeigen und auf ein OK warten.** Die Testeinträge landen echt beim Kunden (CRM,
Newsletter-Liste, E-Mail an das Kundenteam), deshalb soll der User wissen, was abgeschickt wird, und kann
einzelne ausschliessen. Bei **Petitionen/Appellen** ausdrücklich sagen, dass der Test als Unterschrift zählt
(Zähler steigt, Adresse landet auf der Verteilerliste). Danach:

```bash
python $S/forms.py . submit                  # alle mit submit=true
python $S/forms.py . submit --only ID1,ID2   # nur bestimmte
```

Ausgefüllt wird mit hallo@digitalorganizing.ch, «Test d/o Website-Check» und der Nachricht «Automatischer Test
von digital/organizing – bitte ignorieren und löschen.»; Einwilligungs-Checkboxen werden angehakt. Ergebnis pro
Formular: erfolgreich / fehlgeschlagen (Serverfehler, Validierung, keine Reaktion) / unklar (z.B. Captcha).
Auch geprüft: ob die Erfolgsmeldung in der richtigen Sprache kommt und ob nach dem Absenden auf eine Seite der
falschen Sprache weitergeleitet wird. Leitet das Formular nach dem Absenden weiter (typisch bei Tally: ganze Seite
geht auf `/danke`), zählt das als erfolgreich; die Zielseite wird in die Crawl-Queue gelegt.

**Ein Formular nie ein zweites Mal abschicken, nur weil das Test-Skript abgebrochen ist** – erst prüfen, ob der
Eintrag schon angekommen ist (Weiterleitung, Zähler, Formular-Konfiguration). Doppelte Testeinträge sind beim
Kunden lästig, bei Petitionen verfälschen sie die Zahl.

Wenn `forms.py` neue Seiten in die Queue gelegt hat, die Schritte 2 und 3 für diese Seiten wiederholen (die
Skripte überspringen Bekanntes, das geht schnell):

```bash
python $S/crawl.py . && python $S/check_links.py . && python $S/viewports.py .
```

## 4b. Teilen-Links

```bash
python $S/share_links.py .
```

Ein normaler Link-Check sagt bei Teilen-Buttons fast nichts: Facebook- und LinkedIn-Sharer antworten Bots mit 400
bzw. Login-Seite, ein kaputter Telegram-Link leitet still auf telegram.org weiter. `check_links.py` lässt Teilen-
Links deshalb aus; `share_links.py` entschlüsselt jeden Button (WhatsApp, Telegram, Facebook, LinkedIn, X,
Threema, Bluesky, Reddit, Xing, Pinterest, Mastodon, SMS, `mailto:` mit Betreff/Text) und prüft:

- **Format**: Pflichtparameter vorhanden und nicht leer (Telegram `url=`, Facebook `u=`, LinkedIn `url=` …);
  unerwartete Parameter deuten auf ein nicht kodiertes `&`/`#` hin, das den Text abschneidet
- **Text**: vorausgefüllter Text und E-Mail-Betreff in der Sprache der Seite; FR-Elision («la explosion»),
  gemischte Du-/Sie-Form, auffällige Gross-/Kleinschreibung
- **Ziel**: die geteilte Adresse (Parameter oder Link im Text) im echten Browser öffnen – inklusive Kurzlinks,
  die per JavaScript weiterleiten – und prüfen, ob sie in der richtigen Sprache landet; Tracking-Parameter
  (`channel=`, `utm_…`) werden im Bericht angezeigt
- **Link-Vorschau**: Facebook, LinkedIn, WhatsApp, Telegram … führen kein JavaScript aus. Die Vorschau kommt
  also aus dem HTML der geteilten Adresse selbst – bei einem JS-Kurzlink aus der Zwischenseite, nicht aus der
  Kampagnenseite. Geprüft werden dort og:title/description/image (absolute URL, lädt, Grösse), og:url/type
  und die Sprache der OG-Texte

Im Bericht erscheinen die Befunde unter «Teilen-Links» plus eine Tabelle pro Button (Text, Ziel, Vorschau).
Share-Buttons, die nur per JavaScript funktionieren (ohne `href`, z.B. `navigator.share`), findet das Skript nicht –
in den Screenshots der Danke-Seiten nachsehen und bei Bedarf von Hand prüfen.

## 5. Analyse und manuelle Durchsicht

```bash
python $S/analyze.py .
```

Erzeugt `findings.json` mit allen Befunden (kritisch / mittel / Hinweis) und der Matrix Seite × Sprache. Die
Seiten werden über hreflang, den Sprachumschalter und gleiche Pfade zu Zeilen zusammengefasst; fehlende
Sprachversionen erscheinen schraffiert.

Geprüft wird unter anderem:
- **Links**: 404/5xx, tote Domains, Weiterleitungen, fehlende Sprungmarken, verwaiste Seiten, Sitemap-Lücken
- **Übersetzung**: deutsche Texte auf FR-Seiten (Fliesstext, Buttons, Placeholder, alt-Texte, Title, Meta,
  Footer/Navigation), Texte identisch mit der DE-Version, ganze Seiten in falscher Sprache, `<html lang>`
- **Sprachversionen**: fehlende Entsprechungen, Links in die falsche Sprache, hreflang auf tote Seiten,
  Sprachumschalter, der nur zur Startseite führt
- **OG-Tags**: og:title/description/image/url/type/locale, twitter:card; og:locale und og:url passend zur
  Sprache, OG-Texte übersetzt und nicht identisch mit DE, og:image erreichbar/https/≥1200×630
- **Teilen-Links**: siehe Schritt 4b
- **Rechtliches**: Impressum und Datenschutzerklärung pro Sprache vorhanden, erreichbar, in der richtigen
  Sprache und auf (fast) allen Seiten verlinkt
- **Darstellung** und **Technik**: siehe Schritt 3, dazu nicht ladende Bilder/Skripte, JS-Fehler

**Dann die Befunde selbst durchsehen**, bevor der Bericht rausgeht – die Spracherkennung ist heuristisch.
Typische Fehlalarme: Eigennamen und Kampagnennamen («Zürich», «Unia», Initiativtitel), Personennamen,
bewusst deutsche Zitate; nicht übersetzte Texte in ausgeblendeten Vorlage-Elementen (im Browser prüfen, ob
sichtbar – wenn nicht, als Hinweis «Platzhalter aufräumen» stehen lassen). Auch Links **innerhalb** von Texten
prüfen, die die Automatik nicht als Rechtliches erkennt – z.B. «politique de protection des données» im
Einwilligungstext des Formulars, der auf die deutsche Datenschutzerklärung zeigt. Im Zweifel die Screenshots in `screens/` ansehen. Fehlalarme und Ergänzungen in
`$WD/review.json` festhalten, dann `analyze.py` erneut laufen lassen:

```json
{"suppress": ["<finding-id>", "..."],
 "comments": {"<finding-id>": "In der Romandie ist «Impressum» gebräuchlich – mit Kunde klären."},
 "add": [{"sev": "mittel", "cat": "Darstellung", "title": "Hero-Bild auf Mobile abgeschnitten",
          "detail": "", "lang": "fr", "pages": ["https://…"]}]}
```

Nicht übertreiben mit dem Unterdrücken: lieber einen fraglichen Befund mit Kommentar stehen lassen als einen
echten Fehler verstecken. Eigene Beobachtungen aus den Screenshots (abgeschnittene Texte, überlappende
Elemente, leere Bereiche) als `add` ergänzen – das kann die Automatik nicht zuverlässig erkennen.

## 6. Bericht

```bash
python $S/report.py . <ausgabe>/website-check-<domain>-<JJJJ-MM-TT>.html
```

In der Cloud nach `/mnt/user-data/outputs/` schreiben und mit `present_files` übergeben; lokal in den
aktuellen Ordner. Der Bericht ist eine einzelne HTML-Datei (Screenshots eingebettet) mit: Kennzahlen, Matrix
Seite × Sprache (mit Punkten pro Bildschirmgrösse), Befunde nach Kategorie mit Filter nach Schweregrad und
Sprache, Tabelle Impressum/Datenschutz, Formular-Ergebnisse, Teilen-Links, OG-Tabelle, Detail pro Seite, «Nicht prüfbar»
und Vorgehen.

Im Chat danach kurz zusammenfassen (Deutsch, Schweizer Schreibweise mit ss): die wichtigsten kritischen
Befunde in zwei, drei Sätzen, was nicht geprüft werden konnte, und dass die Formular-Testeinträge beim Kunden
gelöscht werden können. Den Bericht nicht im Chat nacherzählen.

## Wenn etwas nicht passt

- **Seite lädt ohne Styles / alle Seiten haben Overflow** → blockierte CDN-Domains, zurück zu Schritt 0.
- **Nur eine Sprache erkannt** → nach der Adresse der anderen Sprachversion fragen und `detect_config.py`
  mit beiden URLs erneut laufen lassen.
- **Cookie-Banner verdeckt alles** → `viewports.py` klickt gängige «Akzeptieren»-Buttons; bei exotischen
  Bannern den Screenshot prüfen und das im Bericht als Hinweis ergänzen.
- **Formular in iframe** (Tally, HubSpot, Mailchimp, RaiseNow …) → wird als «Eingebettetes Formular»
  erkannt; der Anbieter-Host muss in der Cloud ebenfalls freigegeben sein.
- **Sehr grosse Website** (>1000 Seiten) → mit dem User abmachen, ob z.B. Blog-Archive oder Kategorieseiten
  begrenzt werden sollen; Query-Varianten derselben Seite sind bereits auf 5 begrenzt.
- **`Executable doesn't exist at …/chrome-headless-shell`** → pip-Playwright passt nicht zum installierten
  Browser. Die Skripte fallen automatisch auf ein vorhandenes Chromium zurück; sonst `CHROMIUM_PATH` setzen oder
  die zur Browser-Version passende Playwright-Version installieren. Nicht `playwright install` in der Cloud.
- **`ERR_CERT_AUTHORITY_INVALID`** → siehe Schritt 0 (Proxy-CA in `~/.pki/nssdb`).
- **Formular-Test «Frame was detached»** (ältere Skriptversion) → das Formular hat nach dem Absenden die ganze
  Seite weitergeleitet, der Eintrag ist also meist angekommen. Nicht erneut abschicken, sondern das
  Weiterleitungsziel prüfen und `forms_results.json` von Hand korrigieren.
