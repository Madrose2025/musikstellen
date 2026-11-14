#!/usr/bin/env python3
"""
Musikstellen-Monitor: prüft Stellenseiten von Hochschulen, erkennt neue
Musik-Stellen, erzeugt ein HTML-Dashboard (stellen.html) und mailt Neuigkeiten.

Nur Standardbibliothek, Python 3.8+.

Einrichtung:
  1. SOURCES unten prüfen/ergänzen (URLs bitte kontrollieren, Seiten ändern sich).
  2. Mail-Zugang als Umgebungsvariablen setzen:
       SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, MAIL_TO
  3. Erster Lauf:   python3 musikstellen.py          (speichert nur den Ist-Stand)
     Danach täglich: cron  0 7 * * *  python3 /pfad/musikstellen.py
     (oder GitHub Actions mit 'schedule' + Secrets, dann läuft es ohne eigenen Rechner)
"""
import os, re, json, ssl, smtplib, hashlib, datetime, html, sys
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from html.parser import HTMLParser
from email.message import EmailMessage

# (Name, Stellenseite, optional: alle Links zeigen statt nur Musik-Treffer)
SOURCES = [
    # Musikhochschulen CH (deutschsprachig)
    ("ZHdK", "https://www.zhdk.ch/stellen"),
    ("HKB / BFH", "https://www.bfh.ch/de/ueber-die-bfh/jobs/"),
    ("HSLU Musik", "https://www.hslu.ch/de-ch/hochschule-luzern/ueber-uns/jobs-und-karriere/"),
    ("FHNW Musik-Akademie Basel", "https://www.fhnw.ch/de/die-fhnw/jobs"),
    ("Hochschule der Künste / SUPSI-Umfeld, Konsi", "https://www.fhnw.ch/de/die-fhnw/jobs"),
    # Pädagogische Hochschulen CH (deutschsprachig)
    ("PH Zürich", "https://phzh.ch/de/ueber-uns/arbeiten-an-der-phzh/offene-stellen/"),
    ("PH Bern", "https://www.phbern.ch/ueber-die-ph-bern/offene-stellen"),
    ("PH Luzern", "https://www.phlu.ch/ueber-uns/stellen.html"),
    ("PH FHNW", "https://www.fhnw.ch/de/die-fhnw/jobs"),
    ("PH St.Gallen", "https://www.phsg.ch/de/ueber-uns/arbeiten-an-der-phsg"),
    ("PH Thurgau", "https://www.phtg.ch/ueber-uns/offene-stellen/"),
    ("PH Schwyz", "https://www.phsz.ch/ueber-uns/offene-stellen"),
    ("PH Zug", "https://phzg.ch/ueber-uns/offene-stellen"),
    ("PH Schaffhausen", "https://www.phsh.ch/ueber-uns/offene-stellen"),
    ("PH Graubünden", "https://www.phgr.ch/ueber-uns/stellen"),
    ("PH Wallis (dt.)", "https://www.hepvs.ch/de/ueber-uns/offene-stellen"),
    # Ausland
    ("HfM Trossingen", "https://www.mh-trossingen.de/hochschule/stellenangebote"),
    ("Stella Vorarlberg", "https://www.stella.ac.at/jobs"),
    ("WIAM", "https://www.wiam.ch"),  # ggf. durch die direkte Stellen-/Jobs-Unterseite ersetzen
]

KEYWORDS = re.compile(
    r"musik|gesang|stimme|klavier|violin|viola|cello|kontrabass|gitarre|harfe|"
    r"fl[öo]te|oboe|klarinette|fagott|horn|trompete|posaune|tuba|schlagzeug|"
    r"orgel|dirigi|dirigat|chor|orchester|ensemble|korrepetition|rhythmik|"
    r"komposition|jazz|pop|musikp[äa]dagog|musiktheorie|musikwissenschaft|"
    r"instrument|lehrauftrag.*musik|music",
    re.I,
)
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stellen.html")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._txt = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._txt = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href:
            self._txt.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            text = " ".join("".join(self._txt).split())
            self.links.append((self._href, text))
            self._href = None


def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Musikstellen-Monitor)"})
    with urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def scan():
    found, errors = {}, []
    for name, url in SOURCES:
        try:
            p = Links()
            p.feed(fetch(url))
            for href, text in p.links:
                if len(text) < 8 or href.startswith(("#", "mailto:", "javascript:")):
                    continue
                full = urljoin(url, href)
                if KEYWORDS.search(text) or KEYWORDS.search(full):
                    key = hashlib.sha1(full.encode()).hexdigest()[:16]
                    found[key] = {"source": name, "title": text, "url": full}
        except Exception as e:  # eine kaputte Seite stoppt nicht alles
            errors.append(f"{name}: {e}")
    return found, errors


def recent(v, days=14):
    try:
        d = datetime.datetime.strptime(v.get("first_seen", ""), "%d.%m.%Y").date()
        return (datetime.date.today() - d).days <= days
    except ValueError:
        return False  # z. B. "Ausgangsstand"


def render(items, new_keys, errors):
    rows = ""
    for k, v in sorted(items.items(), key=lambda kv: (not recent(kv[1]), kv[1]["source"])):
        badge = "<b style='color:#c00'>NEU</b> " if recent(v) else ""
        rows += (f"<tr><td>{html.escape(v['source'])}</td><td>{badge}"
                 f"<a href='{html.escape(v['url'])}'>{html.escape(v['title'])}</a></td>"
                 f"<td>{v.get('first_seen','')}</td></tr>")
    err = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
    return (f"<!doctype html><meta charset=utf-8><title>Musikstellen</title>"
            "<style>body{font:15px system-ui;max-width:960px;margin:2rem auto;padding:0 1rem}"
            "td,th{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left}</style>"
            f"<h1>Musikstellen</h1><p>Stand: {datetime.datetime.now():%d.%m.%Y %H:%M}</p>"
            f"<table><tr><th>Institution</th><th>Stelle</th><th>Erstmals gesehen</th></tr>{rows}</table>"
            f"{'<h3>Nicht erreichbar</h3><ul>'+err+'</ul>' if err else ''}")


def send_mail(new_items):
    host, to = os.getenv("SMTP_HOST"), os.getenv("MAIL_TO")
    if not (host and to):
        print("SMTP_HOST/MAIL_TO fehlen – keine Mail gesendet."); return
    body = "\n\n".join(f"{v['source']}\n{v['title']}\n{v['url']}" for v in new_items)
    m = EmailMessage()
    m["Subject"] = f"{len(new_items)} neue Musikstelle(n)"
    m["From"] = os.getenv("SMTP_USER", to)
    m["To"] = to
    m.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587))) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
        s.send_message(m)


def main():
    seen = json.load(open(STATE)) if os.path.exists(STATE) else None
    found, errors = scan()
    today = datetime.date.today().strftime("%d.%m.%Y")
    first_run = seen is None
    seen = seen or {}
    new_keys = {k for k in found if k not in seen}
    for k in new_keys:
        seen[k] = {**found[k], "first_seen": "Ausgangsstand" if first_run else today}
    current = {k: seen[k] for k in found}
    json.dump(seen, open(STATE, "w"), ensure_ascii=False, indent=1)
    open(OUT, "w", encoding="utf-8").write(
        render(current, set() if first_run else new_keys, errors))
    print(f"{len(found)} Treffer, {len(new_keys)} neu, {len(errors)} Fehler.")
    if new_keys and not first_run:
        send_mail([found[k] for k in new_keys])


if __name__ == "__main__":
    main()
