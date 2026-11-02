"""Shared helpers for the website-check scripts."""
import glob
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 d-o-website-check")

SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|avif|zip|docx?|xlsx?|pptx?|mp4|mp3|mov|ics|csv|txt|xml|json)$", re.I)
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|_ga|ref$)")


def launch_browser(pw, **kw):
    """Start Chromium. If the pip version of Playwright does not match the installed browser build
    ("Executable doesn't exist"), fall back to an already installed Chromium – e.g. /opt/pw-browsers in
    Claude Code on the web – instead of downloading one. CHROMIUM_PATH overrides the search."""
    try:
        return pw.chromium.launch(**kw)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        pats = [os.environ.get("CHROMIUM_PATH", ""), "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
                "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
        for pat in filter(None, pats):
            for exe in sorted(glob.glob(pat), reverse=True):
                return pw.chromium.launch(executable_path=exe, **kw)
        raise


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def bare_host(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize(url):
    """Drop fragment and tracking params; lower-case host."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    if p.scheme not in ("http", "https"):
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = p.path or "/"  # trailing slash kept: some servers answer /fr/ but 404 on /fr
    return urlunsplit((p.scheme, p.netloc.lower(), path, urlencode(q), ""))


class LangMap:
    """Maps a URL to a configured language.

    config["languages"] = [{"code": "de", "start": "...", "host": "example.ch", "prefix": ""},
                           {"code": "fr", "start": "...", "host": "example.ch", "prefix": "/fr/"}]
    The longest matching prefix on the same host wins, so an empty prefix acts
    as the default language (sites where /de/ is omitted).
    """

    def __init__(self, config):
        self.langs = config["languages"]
        self.source = config.get("source_language") or self.langs[0]["code"]
        self.extra_hosts = {bare_host(h) for h in config.get("extra_internal_hosts", [])}

    @property
    def codes(self):
        return [l["code"] for l in self.langs]

    @property
    def hosts(self):
        return {bare_host(l["host"]) for l in self.langs} | self.extra_hosts

    def is_internal(self, url):
        try:
            return bare_host(urlsplit(url).hostname) in self.hosts
        except ValueError:
            return False

    def lang_of(self, url):
        try:
            p = urlsplit(url)
        except ValueError:
            return None
        host, path = bare_host(p.hostname), (p.path or "/")
        best, best_len = None, -1
        for l in self.langs:
            if bare_host(l["host"]) != host:
                continue
            pre = l.get("prefix") or ""
            pre_n = pre.rstrip("/")
            if pre == "" or path == pre_n or path.startswith(pre_n + "/"):
                if len(pre) > best_len:
                    best, best_len = l["code"], len(pre)
        return best

    def strip_prefix(self, url):
        """Path without language prefix, used to pair pages with shared slugs."""
        p = urlsplit(url)
        code = self.lang_of(url)
        path = p.path or "/"
        for l in self.langs:
            if l["code"] == code and l.get("prefix"):
                pre = l["prefix"].rstrip("/")
                if path.startswith(pre):
                    path = path[len(pre):] or "/"
        return path.rstrip("/") or "/"


# ---------------------------------------------------------------- language heuristics
STOP = {
    "de": set("""und der die das den dem des ist nicht mit für auf sich ein eine einer eines
        auch als wir sie ihr ihre unsere unser werden wird wurde sind bei nach aus oder
        wie noch nur mehr über zum zur vom können kann durch diese dieser hier jetzt
        haben hat alle euch uns ich du sein seine gegen ohne zwischen schon wenn dass weil""".split()),
    "fr": set("""le la les et des du un une est pas pour avec sur dans que qui nous vous
        ils elles son sa ses leur leurs au aux ce cette ces plus ou mais par être sont
        été tout tous notre nos votre vos contre sans entre déjà quand parce aussi très
        je tu il elle ont avons faire peut""".split()),
    "it": set("""il lo la gli le e di del della dei delle un una è non per con su nel
        che chi noi voi loro sono essere anche più ma o come alla alle al ai questo
        questa nostra nostro vostra vostro contro senza tra già quando perché""".split()),
    "en": set("""the and of to in is are not for with on that this we you they our your
        be was were have has more or but by from all can will about into without
        between already when because also very""".split()),
}
# Short UI strings that are unmistakably German (buttons, labels, footers).
DE_UI = set()
DE_UI |= {w.lower() for w in re.split(r"\s+", """Absenden Senden Weiter Zurück Spenden Anmelden Suchen Suche
    Menü Schliessen Vorname Nachname Nachricht Datenschutz Datenschutzerklärung Impressum Kontakt
    Pflichtfeld Startseite Weiterlesen Teilen Unterschreiben Mitmachen Herunterladen Strasse PLZ Ort
    Telefon Anrede Januar Februar März Juni Juli Oktober Dezember Montag Dienstag Mittwoch
    Donnerstag Freitag Samstag Sonntag""")}
DE_UI |= {"ja", "nein", "herr", "frau", "land", "alle", "mehr", "aktuell", "projekte", "stellen", "suchen"}
DE_PHRASES = ["mehr erfahren", "jetzt spenden", "newsletter abonnieren", "mehr lesen", "über uns",
              "bitte wählen", "jetzt unterschreiben", "e-mail-adresse"]

WORD = re.compile(r"[a-zA-ZÀ-ÿ'’]+")


def detect_lang(text, candidates=("de", "fr", "it", "en")):
    """Return (lang, confidence 0..1) using stopword counts; (None, 0) if unclear."""
    words = [w.lower().strip("'’") for w in WORD.findall(text)]
    if not words:
        return None, 0.0
    scores = {c: sum(1 for w in words if w in STOP[c]) for c in candidates if c in STOP}
    # German-specific letters are a strong signal in otherwise short strings
    if "de" in scores:
        scores["de"] += 2 * len(re.findall(r"[äöüß]", text.lower())) if not re.search(r"[éèêàç]", text) else 0
    total = sum(scores.values())
    if total == 0:
        return None, 0.0
    lang = max(scores, key=scores.get)
    conf = scores[lang] / total
    return lang, conf


def looks_german_short(text):
    t = text.strip().lower()
    if not t:
        return False
    if t in DE_UI or any(p in t for p in DE_PHRASES):
        return True
    return False


def looks_like(text, target, page_lang):
    """True if text appears to be written in `target` although the page is `page_lang`."""
    t = text.strip()
    if len(t) < 2 or re.fullmatch(r"[\W\d_]+", t):
        return False
    words = WORD.findall(t)
    if len(words) >= 4:
        lang, conf = detect_lang(t)
        return lang == target and conf >= 0.6
    if target == "de" and page_lang != "de":
        return looks_german_short(t) or (bool(re.search(r"[äöüß]", t)) and not re.search(r"[éèêàçù]", t))
    return False


LANG_LABELS = {
    "de": ["de", "deutsch", "german", "allemand", "tedesco"],
    "fr": ["fr", "français", "francais", "french", "französisch", "francese"],
    "it": ["it", "italiano", "italian", "italienisch", "italien"],
    "en": ["en", "english", "englisch", "anglais", "inglese"],
    "rm": ["rm", "rumantsch", "romanisch"],
}


def label_lang(text):
    t = (text or "").strip().lower()
    for code, labels in LANG_LABELS.items():
        if t in labels:
            return code
    return None


def foreign_strength(text, target, page_lang):
    """'strong', 'weak' or None: how sure we are that text is in `target` instead of page_lang."""
    if not looks_like(text, target, page_lang):
        return None
    t = text.strip()
    if len(WORD.findall(t)) >= 4 or looks_german_short(t):
        return "strong"
    return "weak"  # e.g. a single word with an umlaut — often a proper noun like "Zürich"
