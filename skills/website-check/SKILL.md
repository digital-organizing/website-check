---
name: website-check
description: Gründlicher QA-Check einer (mehrsprachigen) Website mit HTML-Bericht auf Deutsch – defekte Links, Formulare (echte Testeinträge), vergessene Übersetzungen (meist DE→FR), OG-Tags, Teilen-Buttons (WhatsApp, Telegram, Facebook, LinkedIn, E-Mail … inkl. Link-Vorschau und Danke-Seiten), Impressum/Datenschutz, Darstellung auf Mobile/Tablet/Desktop und Browser (Chrome, Firefox, Safari auf Desktop, Android, iPhone/iPad), als Matrix Seite × Sprache. Verwende diesen Skill immer, wenn jemand eine Website prüfen, testen, abnehmen oder vor dem Go-live kontrollieren will – auch bei «schau mal ob auf der Seite alles funktioniert», «Website-Abnahme», «Link-Check», «sind alle Übersetzungen drin», «check die FR-Version», «funktionieren die Formulare», «OG-Tags prüfen», «Share-Links prüfen», «läuft die Seite auf allen Browsern», «geht das auf dem iPhone / in Safari», «QA vor Launch» oder wenn nur eine URL mit der Bitte um Kontrolle kommt.
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
3b. Browser + Geräte → Chromium, Firefox, WebKit × Desktop/Mobile (Hintergrund)
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

- Fehlt das Python-Paket `playwright` (Claude Code im Web hat nur die Node-Version), mit der **gleichen Version**
  wie das vorinstallierte Node-Playwright installieren (`npm ls -g playwright` → z.B. `pip install playwright==1.63.0`),
  sonst passt es nicht zum vorhandenen Chromium. `preflight.py` gibt den passenden Befehl aus.
- **Für den Browsertest (3b)** zusätzlich Firefox und WebKit, **in einen eigenen Ordner**, damit das vorinstallierte
  Chromium unangetastet bleibt:

      export PLAYWRIGHT_BROWSERS_PATH=$WD/../pw-browsers && mkdir -p $PLAYWRIGHT_BROWSERS_PATH
      python -m playwright install firefox webkit
      python -m playwright install-deps webkit        # fehlende Systembibliotheken (apt, root), einmalig
      ln -s /opt/pw-browsers/chromium* $PLAYWRIGHT_BROWSERS_PATH/   # nur wo es /opt/pw-browsers gibt

  `browsers.py` findet `$WD/../pw-browsers` auch ohne Umgebungsvariable. Der Download braucht Netzwerkzugang zu den
  Playwright-Download-Servern (in claude.ai meist nicht freigegeben) und `install-deps` braucht apt. Klappt eines
  davon nicht: **trotzdem weitermachen** – `browsers.py` testet dann nur mit den verfügbaren Engines und der Bericht
  sagt ausdrücklich, was nicht getestet wurde. `preflight.py` zeigt am Ende an, welche Engines bereitstehen.
- **Firefox und Proxy-Zertifikat** (Cloud): Firefox nutzt einen eigenen Zertifikatsspeicher. `browsers.py` legt
  dafür automatisch ein Profil an, das der Proxy-CA vertraut (`certutil`, Paket `libnss3-tools`). WebKit übernimmt
  die System-Zertifikate. Nie die Zertifikatsprüfung abschalten.

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

`viewports.py` scrollt jede Seite einmal bis ganz nach unten (Lazy Loading) und misst dann pro Seite ×
Bildschirmgrösse horizontales Überlaufen (mit den verursachenden Elementen),
ob die Navigation erreichbar ist (öffnet Burger-Menüs selbst), kaputte Bilder, und macht Screenshots. Das ist
der langsamste Schritt (Seiten × 3) – im Hintergrund laufen lassen.

## 3b. Browser und Geräte

Praktisch alle Browser laufen auf einer von drei Engines: **Chromium** (Chrome, Edge, Brave, Opera, Samsung
Internet), **Gecko** (Firefox) und **WebKit** (Safari, und auf iPhone/iPad *jeder* Browser, auch Chrome und
Firefox). Wer diese drei auf Desktop und Mobile prüft, deckt fast alle Besucher*innen ab.

```bash
nohup python $S/browsers.py . [--pages main|all] [--extra small,dark,slow,zoom,inapp] > br.out 2>&1 &
python $S/browsers.py $WD --urls https://beispiel.ch/ https://beispiel.ch/fr/   # schnell, ohne Crawl
```

**Seiten:** Standard ist `--pages main` = Startseite pro Sprache, Testseiten der Formulare, Danke-Seiten und je eine
Seite mit Teilen-Buttons (max. 25). Browser-Unterschiede hängen an Vorlagen, nicht an einzelnen Seiten; die
Darstellung *aller* Seiten prüft schon `viewports.py`. `--pages all` nur bei kleinen Websites (Seiten × 7
Kombinationen × ca. 10 s). Am besten **nach** Schritt 4 (Inventar) und 4b laufen lassen, dann kennt das Skript
Formulare, Danke-Seiten und Teilen-Buttons.

Standard-Kombinationen (in `config.json` → `browsers` als Liste von ids einschränkbar):

| Browser | Gerät | Engine |
|---|---|---|
| Chrome/Edge | Desktop 1440 px | Chromium |
| Chrome | Android (Pixel 7) | Chromium |
| Firefox | Desktop 1440 px | Gecko |
| Firefox | Android (412 px, Touch, UA – Playwright-Firefox kennt kein «isMobile») | Gecko |
| Safari | macOS 1440 px | WebKit |
| Safari | iPhone 15 | WebKit |
| Safari | iPad | WebKit |

Geprüft wird pro Seite × Kombination (nach einmaligem Scrollen bis ganz nach unten, wegen Lazy Loading):
- lädt (Status), nicht abgefangene JavaScript-Fehler, fehlgeschlagene Ressourcen
- kein horizontales Überlaufen (mit verursachenden Elementen) **und nichts links abgeschnitten** (Inhalt mit
  negativer Position erzeugt keinen Scrollbalken und fällt sonst nicht auf), keine kaputten Bilder
- Webfonts tatsächlich geladen (`document.fonts`, nur Schriften, die sichtbarer Text auch verwendet –
  deklarierte, aber unbenutzte Schriften bleiben legitimerweise «unloaded»)
- Sprachumschalter sichtbar und innerhalb der Bildschirmbreite (öffnet bei Bedarf das Burger-Menü)
- Formulare (auch Tally/HubSpot im iframe): Felder sichtbar, ein Textfeld per Placeholder antippen und eintippen
  (nie ein Auswahlfeld), Absende-Button innerhalb des Rahmens, iframe nicht zu niedrig – **nie abschicken**. Bei
  Tally-Formularen mit aktiven «Teil-Antworten» wird nicht getippt (die würden sonst gespeichert).
- Akkordeons/Tabs öffnen sich (erstes Element anklicken, Höhenänderung messen)
- Teilen-Buttons sichtbar, innerhalb des Bildschirms und antippbar (nicht verdeckt)
- Screenshot vom **ersten Bildschirm** (nicht ganzseitig – siehe Fehlalarme)

Optionale Zusatztests mit `--extra` (für Kampagnenseiten empfohlen: `small,dark,slow`):
- `small`: iPhone SE (375 × 667) und 320 px Breite: passt das Formular, bricht der Titel sauber um?
- `dark`: `prefers-color-scheme: dark` und Chromiums automatisches Abdunkeln (wie Samsung Internet). Kontrast
  wird gemessen; gemeldet wird nur, was im Dark Mode *schlechter* ist als hell
- `slow`: 3G-Drosselung (nur Chromium, nur Seiten mit Formular): Zeit bis ein Formularfeld sichtbar ist (> 10 s = Befund)
- `zoom`: Browser-Zoom 150 % und 200 % (= 960 bzw. 720 CSS-Pixel bei 1440 px Bildschirm): Überlaufen,
  abgeschnittene Texte
- `inapp`: User-Agent von Instagram, Facebook und LinkedIn (nur Annäherung, die echten App-Browser verhalten sich
  teils anders, z.B. kein Pop-up für `mailto:`; WhatsApp öffnet Links im normalen Browser)

`analyze.py` meldet in der Kategorie **«Browser»** nur, was in einer Engine scheitert und in einer anderen nicht
(«Formular lädt nicht – nur Safari iPhone 15, Safari iPad»). Scheitert ein Test in *allen* Browsern einer
Gerätegrösse, ist das kein Browserproblem: dann unter «Darstellung» (sofern nicht schon durch `viewports.py`
abgedeckt). Zusatztests erscheinen mit ihrer Bedingung im Titel («– Chrome 320 px»), seltene Fälle (320 px, Zoom,
App-Browser) mit tieferem Schweregrad.

Im Bericht erscheint ein Abschnitt «Browser und Geräte» mit einer ✓/✗-Tabelle, Screenshots vom ersten
Bildschirm nebeneinander und dem Hinweis auf die Grenzen. Nur diesen Teil als PDF (A4 quer):
`python $S/report.py . out.pdf --only browser` (nach `analyze.py`).

**Befunde selbst gegenprüfen**, bevor sie in den Bericht gehen: Ein Test, der nur in einer Engine scheitert, ist
entweder ein echter Kompatibilitätsfehler oder ein Artefakt der Automatisierung. Im Zweifel die betroffene Seite in
dieser Engine mit einem kurzen Playwright-Skript nachstellen und den Screenshot ansehen.

**Grenzen offen benennen:** Getestet wird mit aktuellen Engines und Geräte-Emulation unter Linux. Echte Geräte,
ältere Versionen (z.B. iOS 15/16), die Bildschirmtastatur über dem Formular und echte App-Browser lassen sich so
nicht prüfen. Dafür braucht es einen Testdienst mit echten Geräten (BrowserStack, LambdaTest, mit Konto und
Netzwerkfreigabe) oder einen kurzen Test von Hand auf einem iPhone und einem Android-Gerät.

## 4. Formulare – erst zeigen, dann echt abschicken

```bash
python $S/forms.py . inventory
```

Bei eingebetteten Formularen (Tally, HubSpot …) liest das Inventar zusätzlich die Formular-Konfiguration und
zeigt interne Weiterleitungsziele an («leitet evtl. weiter auf …/danke»). **Tally** wird direkt aus der
Konfiguration (`tally.so/r/<id>`) gelesen: sichtbare Felder, ob das Formular geschlossen ist, Formular-Sprache,
«Teil-Antworten» und das Weiterleitungsziel – auch wenn es auf eine fremde oder Staging-Domain zeigt (z.B.
`projekt.webflow.io/danke`, ein häufiger Fehler nach dem Go-live; dann wird das Live-Gegenstück geprüft). Diese Danke-Seiten kommen automatisch in
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
python $S/crawl.py . && python $S/check_links.py . && python $S/viewports.py . && python $S/browsers.py .
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
- **Browser**: siehe Schritt 3b

**Dann die Befunde selbst durchsehen**, bevor der Bericht rausgeht – die Spracherkennung ist heuristisch.
Typische Fehlalarme: Eigennamen und Kampagnennamen («Zürich», «Unia», Initiativtitel), Personennamen,
bewusst deutsche Zitate; nicht übersetzte Texte in ausgeblendeten Vorlage-Elementen (im Browser prüfen, ob
sichtbar – wenn nicht, als Hinweis «Platzhalter aufräumen» stehen lassen). Auch Links **innerhalb** von Texten
prüfen, die die Automatik nicht als Rechtliches erkennt – z.B. «politique de protection des données» im
Einwilligungstext des Formulars, der auf die deutsche Datenschutzerklärung zeigt. Im Zweifel die Screenshots in `screens/` ansehen.

Weitere typische Fehlalarme:
- **Lazy Loading:** Bilder mit `loading="lazy"` (z.B. Social-Media-Icons im Footer) und iframes mit
  `loading="lazy"` / `data-tally-src` haben vor dem Hinscrollen die Breite 0 bzw. sind noch nicht geladen. Die
  Skripte scrollen vor der Messung nach unten; bei eigenen Nachprüfungen ebenso. Tally lädt erst, wenn der Rahmen
  *im Bild ist* – wer schnell daran vorbeiscrollt, sieht nie ein Formular. Nie «unsichtbar» oder «lädt nicht»
  melden, ohne das geprüft zu haben.
- **Ganzseitige Screenshots** zeigen Elemente mit `position: fixed/sticky` (schwebende Buttons, Cookie-Banner) an
  falscher Stelle über dem Inhalt. Überlappungen nur aus Screenshots vom ersten Bildschirm oder mit gemessenen
  Positionen beim Scrollen melden.
- **Testskript-Artefakte:** Ein geöffnetes Dropdown im Screenshot oder ein «nicht editierbares» Feld kommt oft
  davon, dass das Skript in ein Auswahlfeld (combobox) statt in ein Textfeld getippt hat. Felder über den
  Placeholder ansprechen und Tasten an das Feld selbst schicken (`locator.press_sequentially`), nicht an die Seite
  – sonst kommt z.B. in Firefox bei iframes von fremden Domains kein Text an. Fehlalarme und Ergänzungen in
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
Sprache, Tabelle Impressum/Datenschutz, Formular-Ergebnisse, Teilen-Links, Browser und Geräte, OG-Tabelle, Detail pro Seite, «Nicht prüfbar»
und Vorgehen.

Im Chat danach kurz zusammenfassen (Deutsch, Schweizer Schreibweise mit ss): die wichtigsten kritischen
Befunde in zwei, drei Sätzen, was nicht geprüft werden konnte (auch: welche Browser-Engines fehlten), und – falls welche abgeschickt
wurden – dass die Formular-Testeinträge beim Kunden gelöscht werden können. Den Bericht nicht im Chat nacherzählen.

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
- **Website hat sich während des Checks geändert** → `Last-Modified` bzw. `ETag` der Startseite zu Beginn notieren
  (`curl -sI`; fehlen beide, einen Hash des HTML ohne Zähler/Nonces) und vor dem Bericht erneut prüfen. Hat er sich
  geändert, den Check auf dem neuen Stand wiederholen (neuer Arbeitsordner) und im Bericht «Seit dem letzten Check
  behoben» auflisten. Beim zweiten Durchgang manuelle Befunde aus `review.json` einzeln gegen den aktuellen Stand
  prüfen, nicht einfach übernehmen.
- **`ModuleNotFoundError: playwright`** → siehe Voraussetzungen (pip-Version passend zum Node-Playwright).
- **WebKit startet nicht («missing dependencies»)** → `python -m playwright install-deps webkit`; geht das nicht,
  ohne WebKit weitermachen (der Bericht vermerkt es).
- **Firefox: `SEC_ERROR_UNKNOWN_ISSUER`** → Profil mit Proxy-CA: `certutil` installieren (`libnss3-tools`) und
  `$WD/firefox-profile` löschen, dann `browsers.py` erneut starten.
- **Formular-Test «Frame was detached»** (ältere Skriptversion) → das Formular hat nach dem Absenden die ganze
  Seite weitergeleitet, der Eintrag ist also meist angekommen. Nicht erneut abschicken, sondern das
  Weiterleitungsziel prüfen und `forms_results.json` von Hand korrigieren.
