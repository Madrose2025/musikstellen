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

# (Name, Startseite oder direkte Stellenseite). Das Skript sucht auf der Startseite
# selbst nach Links wie "Stellen", "Jobs", "Offene Stellen", "Karriere".
SOURCES = [
    # Musikhochschulen CH
    ("ZHdK", "https://www.zhdk.ch/jobs"),
    ("HKB / BFH", "https://www.bfh.ch"),
    ("HSLU", "https://www.hslu.ch"),
    ("FHNW (Musik-Akademie Basel + PH FHNW)", "https://www.fhnw.ch"),
    # Pädagogische Hochschulen CH (deutschsprachig)
    ("PH Zürich", "https://phzh.ch"),
    ("PH Bern", "https://www.phbern.ch"),
    ("PH Luzern", "https://www.phlu.ch"),
    ("PH St.Gallen", "https://www.phsg.ch"),
    ("PH Thurgau", "https://www.phtg.ch"),
    ("PH Schwyz", "https://www.phsz.ch"),
    ("PH Zug", "https://www.phzg.ch"),
    ("PH Schaffhausen", "https://www.phsh.ch"),
    ("PH Graubünden", "https://www.phgr.ch"),
    ("PH Wallis (dt.)", "https://www.hepvs.ch"),
    # Ausland
    ("HfM Trossingen", "https://www.mh-trossingen.de"),
    ("Stella Vorarlberg", "hhttps://stella-musikhochschule.ac.at/ueber-uns/karriere-bei-stella"),
    ("WIAM", "https://www.wiam.ch"),
]

JOB_RE = re.compile(r"stellen|\bjobs?\b|karriere|career|offene|vacanc|ausschreibung", re.I)

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


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.5",
}


def fetch(url):
    req = Request(url, headers=HEADERS)
    try:
        ctx = ssl.create_default_context()
        r = urlopen(req, timeout=30, context=ctx)
    except ssl.SSLError:  # fehlerhaftes Zertifikat: öffentliche Seite trotzdem lesen
        r = urlopen(req, timeout=30, context=ssl._create_unverified_context())
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        r = urlopen(req, timeout=30, context=ssl._create_unverified_context())
    with r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def get_links(url):
    p = Links()
    p.feed(fetch(url))
    out = []
    for href, text in p.links:
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        out.append((urljoin(url, href), text))
    return out


def scan():
    found, errors = {}, []
    for name, start in SOURCES:
        try:
            links = get_links(start)
            # Stellenseiten finden (Startseite selbst zählt, wenn sie schon eine ist)
            pages = [start] if JOB_RE.search(start) else []
            for full, text in links:
                if JOB_RE.search(text) and 3 <= len(text) <= 40 and full not in pages:
                    pages.append(full)
                if len(pages) >= 4:
                    break
            if not pages:
                errors.append(f"{name}: keine Stellenseite gefunden – bitte direkte URL in SOURCES eintragen")
                continue
            for page in pages:
                try:
                    page_links = links if page == start else get_links(page)
                except Exception as e:
                    errors.append(f"{name} ({page}): {e}")
                    continue
                for full, text in page_links:
                    text = " ".join(text.split())
                    if len(text) < 8:
                        continue
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
