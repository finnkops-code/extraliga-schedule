import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

HOMEPAGE_URL = "https://extraliga.baseball.cz/"
SCHEDULE_URL = "https://extraliga.baseball.cz/rozpis-vysledky"

TEAM_ALIASES = {
    "Draci Brno": "DRA", "Draci": "DRA",
    "Kotlářka Praha": "KOT", "Kotlářka": "KOT",
    "Arrows Ostrava": "ARR", "Arrows": "ARR",
    "Eagles Praha": "EAG", "Eagles": "EAG",
    "Cardion Hroši Brno": "HRO", "Hroši": "HRO",
    "SaBaT Praha": "SAB", "SaBaT": "SAB",
    "Sokol Hluboká": "SOK", "Hluboká": "SOK",
    "Třebíč Nuclears": "NUC", "Nuclears": "NUC",
}

KNOWN_CODES = ["DRA", "KOT", "ARR", "EAG", "HRO", "SAB", "SOK", "NUC"]

DUTCH_MONTHS = ["", "januari", "februari", "maart", "april", "mei", "juni",
                "juli", "augustus", "september", "oktober", "november", "december"]

CZECH_WEEKDAYS_SHORT = {
    "Po": "maandag", "Út": "dinsdag", "St": "woensdag",
    "Čt": "donderdag", "Pá": "vrijdag", "So": "zaterdag", "Ne": "zondag",
}

CZECH_WEEKDAYS_LONG = {
    "Pondělí": ("Po", "maandag"), "Úterý": ("Út", "dinsdag"),
    "Středa": ("St", "woensdag"), "Čtvrtek": ("Čt", "donderdag"),
    "Pátek": ("Pá", "vrijdag"), "Sobota": ("So", "zaterdag"),
    "Neděle": ("Ne", "zondag"),
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_date_from_buf(buf: str):
    """Pakt datum uit tekst, zowel lang (Pátek) als kort (Pá) formaat."""
    # Lang formaat: Pátek 10. 4. 2026
    for long, (short, nl) in CZECH_WEEKDAYS_LONG.items():
        m = re.search(rf"{long}\s+(\d+)\.\s+(\d+)\.\s+(\d{{4}})", buf)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                dt = datetime(year, month, day)
                return dt.strftime("%Y-%m-%d"), f"{nl} {day} {DUTCH_MONTHS[month]}"
            except ValueError:
                pass
    # Kort formaat: Pá 10. 4. 2026
    m = re.search(
        r"([A-ZÁČĎÉÍŇÓŘŠŤŮÚŽÝ][a-záčďéíňóřšťůúžý])\s+(\d+)\.\s+(\d+)\.\s+(\d{4})",
        buf,
    )
    if m:
        wday_cz, day, month, year = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            dt = datetime(year, month, day)
            nl = CZECH_WEEKDAYS_SHORT.get(wday_cz, wday_cz)
            return dt.strftime("%Y-%m-%d"), f"{nl} {day} {DUTCH_MONTHS[month]}"
        except ValueError:
            pass
    return None, None


def extract_score(buf: str):
    """Verwijder tijden (HH:MM) eerst, zoek dan score X:Y."""
    cleaned = re.sub(r'\b([01]?\d|2[0-3]):[0-5]\d\b', '', buf)
    m = re.search(r'\b(\d+):(\d+)\b', cleaned)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def clean_team_name(raw: str):
    raw = raw.strip()
    for code in KNOWN_CODES:
        if raw.endswith(code):
            for full_name, c in TEAM_ALIASES.items():
                if c == code and raw.startswith(full_name):
                    return full_name, code
            return raw[:-3].strip(), code
    for full_name, code in TEAM_ALIASES.items():
        if raw.startswith(full_name):
            return full_name, code
    return raw, ""


# ── Homepage parser: scrapet wedstrijdblokken uit de <li>-lijst ─────────────

class HomepageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._games = []
        self._in_schedule_ul = False
        self._schedule_ul_depth = None
        self._depth_ul = 0
        self._in_li = False
        self._text_buf = ""
        self._img_codes = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "ul":
            self._depth_ul += 1
            if self._in_schedule_ul and self._schedule_ul_depth is None:
                self._schedule_ul_depth = self._depth_ul
        if tag == "li":
            if self._in_schedule_ul and self._depth_ul == self._schedule_ul_depth:
                self._in_li = True
                self._text_buf = ""
                self._img_codes = []
        if tag == "img" and self._in_li:
            alt = attrs_d.get("alt", "")
            src = attrs_d.get("src", "")
            if "/team/icon/" in src and alt in KNOWN_CODES:
                self._img_codes.append(alt)

    def handle_endtag(self, tag):
        if tag == "ul":
            if self._schedule_ul_depth == self._depth_ul:
                self._schedule_ul_depth = None
                self._in_schedule_ul = False
            self._depth_ul -= 1
        if tag == "li" and self._in_li:
            self._in_li = False
            self._flush_li()

    def handle_data(self, data):
        stripped = data.strip()
        if stripped and ("Následující zápasy" in stripped):
            self._in_schedule_ul = True
        if self._in_li:
            self._text_buf += " " + data

    def _flush_li(self):
        buf = self._text_buf.strip()
        if not buf or len(self._img_codes) < 2:
            return

        datum_iso, dag_nl = parse_date_from_buf(buf)
        if not datum_iso:
            return

        # Tijdstip
        tijdstip = None
        tm = re.search(r'\b([01]?\d|2[0-3]):[0-5]\d\b', buf)
        if tm:
            tijdstip = tm.group(0)

        # Score (na tijden verwijderen)
        score_thuis, score_uit = extract_score(buf)
        gespeeld = score_thuis is not None

        thuis_code = self._img_codes[0]
        uit_code   = self._img_codes[1]
        thuis_naam = next((k for k, v in TEAM_ALIASES.items() if v == thuis_code and " " in k), thuis_code)
        uit_naam   = next((k for k, v in TEAM_ALIASES.items() if v == uit_code   and " " in k), uit_code)

        self._games.append({
            "datum":       datum_iso,
            "dag":         dag_nl,
            "tijdstip":    tijdstip,
            "thuis":       thuis_naam,
            "thuis_code":  thuis_code,
            "uit":         uit_naam,
            "uit_code":    uit_code,
            "score_thuis": score_thuis,
            "score_uit":   score_uit,
            "gespeeld":    gespeeld,
            "fase":        "",
            "locatie":     "",
        })

    def get_games(self):
        return self._games


# ── Schedule-tabel parser (voor programma) ──────────────────────────────────

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._cur_table = None
        self._cur_row = None
        self._cur_cell = None
        self._in_cell = False
        self._nest = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._cur_table is None:
                self._cur_table = []
                self._nest = 0
            else:
                self._nest += 1
        elif tag == "tr" and self._nest == 0 and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._nest == 0 and self._cur_row is not None:
            self._cur_cell = ""
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "table":
            if self._nest == 0 and self._cur_table is not None:
                self.tables.append(self._cur_table)
                self._cur_table = None
                self._cur_row = None
                self._cur_cell = None
                self._in_cell = False
            else:
                self._nest = max(0, self._nest - 1)
        elif tag == "tr" and self._nest == 0:
            if self._cur_row and self._cur_table is not None:
                if any(c.strip() for c in self._cur_row):
                    self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._nest == 0:
            if self._cur_row is not None and self._cur_cell is not None:
                self._cur_row.append(self._cur_cell.strip())
            self._cur_cell = None
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell and self._cur_cell is not None:
            self._cur_cell += data


def extract_schedule_from_table(rows):
    games = []
    current_datum = None
    current_dag = None

    for row in rows:
        if not row or len(row) < 7:
            continue
        if row[0].strip() == "Datum":
            continue

        datum_raw = row[0].strip()
        if datum_raw:
            d, dg = parse_date_from_buf(datum_raw)
            if d:
                current_datum, current_dag = d, dg

        if not current_datum:
            continue

        tijdstip_raw = row[3].strip() if len(row) > 3 else ""
        thuis_raw    = row[4].strip() if len(row) > 4 else ""
        uit_raw      = row[5].strip() if len(row) > 5 else ""
        score_raw    = row[6].strip() if len(row) > 6 else ""
        locatie_raw  = row[7].strip() if len(row) > 7 else ""

        thuis_naam, thuis_code = clean_team_name(thuis_raw)
        uit_naam,   uit_code   = clean_team_name(uit_raw)
        score_thuis, score_uit = extract_score(score_raw)
        gespeeld = score_thuis is not None

        tijdstip = tijdstip_raw if re.match(r"\d{1,2}:\d{2}", tijdstip_raw) else None

        games.append({
            "datum":       current_datum,
            "dag":         current_dag,
            "tijdstip":    tijdstip,
            "thuis":       thuis_naam,
            "thuis_code":  thuis_code,
            "uit":         uit_naam,
            "uit_code":    uit_code,
            "score_thuis": score_thuis,
            "score_uit":   score_uit,
            "gespeeld":    gespeeld,
            "fase":        row[1].strip() if len(row) > 1 else "",
            "locatie":     locatie_raw,
        })
    return games


def speelronde_bounds():
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()
    weekday = today.weekday()
    days_since_friday = (weekday - 4) % 7
    friday = today - timedelta(days=days_since_friday)
    sunday = friday + timedelta(days=2)
    return friday, sunday


def main():
    friday, sunday = speelronde_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Meest recente speelronde: {friday} (vr) t/m {sunday} (zo)")

    # ── Uitslagen van de homepage ──
    print(f"\nOphalen homepage: {HOMEPAGE_URL}")
    hp_html = fetch_html(HOMEPAGE_URL)
    hp_parser = HomepageParser()
    hp_parser.feed(hp_html)
    hp_games = hp_parser.get_games()
    print(f"Wedstrijden op homepage: {len(hp_games)}")

    uitslagen = []
    for g in hp_games:
        if not g["datum"]:
            continue
        gd = datetime.strptime(g["datum"], "%Y-%m-%d").date()
        if g["gespeeld"] and friday <= gd <= sunday:
            uitslagen.append(g)

    uitslagen.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))
    print(f"Uitslagen in speelronde: {len(uitslagen)}")
    for u in uitslagen:
        print(f"  {u['datum']} {u['thuis_code']} {u['score_thuis']}-{u['score_uit']} {u['uit_code']}")

    # ── Programma van de schedule-pagina ──
    print(f"\nOphalen programma: {SCHEDULE_URL}")
    sc_html = fetch_html(SCHEDULE_URL)
    tp = TableParser()
    tp.feed(sc_html)

    all_schedule = []
    for table in tp.tables:
        games = extract_schedule_from_table(table)
        if len(games) > len(all_schedule):
            all_schedule = games

    programma = []
    for g in all_schedule:
        if not g["datum"]:
            continue
        gd = datetime.strptime(g["datum"], "%Y-%m-%d").date()
        if not g["gespeeld"] and gd > today:
            programma.append(g)

    programma.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))
    programma = programma[:12]
    print(f"Aankomende wedstrijden: {len(programma)}")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']} {p['thuis_code']} vs {p['uit_code']}")

    if not uitslagen:
        print("\n⚠️  Geen uitslagen gevonden — homepage toont mogelijk geen scores deze week.")

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": HOMEPAGE_URL,
        "speelronde": {"van": str(friday), "tot": str(sunday)},
        "uitslagen": uitslagen,
        "programma": programma,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen")
    print(f"   Uitslagen deze speelronde : {len(uitslagen)}")
    print(f"   Aankomende wedstrijden    : {len(programma)}")


if __name__ == "__main__":
    main()
