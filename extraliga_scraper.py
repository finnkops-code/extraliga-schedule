import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

SCHEDULE_URL = "https://extraliga.baseball.cz/rozpis-vysledky"

# Team codes die in de tabel voorkomen (3-letter afkorting)
# We extraheren ze uit de HTML — deze mapping is als fallback
TEAM_ALIASES = {
    "Draci Brno": "DRA",
    "Draci": "DRA",
    "Kotlářka Praha": "KOT",
    "Kotlářka": "KOT",
    "Arrows Ostrava": "ARR",
    "Arrows": "ARR",
    "Eagles Praha": "EAG",
    "Eagles": "EAG",
    "Cardion Hroši Brno": "HRO",
    "Hroši": "HRO",
    "SaBaT Praha": "SAB",
    "SaBaT": "SAB",
    "Sokol Hluboká": "SOK",
    "Hluboká": "SOK",
    "Třebíč Nuclears": "NUC",
    "Nuclears": "NUC",
}

# Tsjechische dag/maand-namen → nummers
CZECH_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4,
    "května": 5, "června": 6, "července": 7, "srpna": 8,
    "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
    # Verkorte vormen die in de tabel voorkomen
    "1.": 1, "2.": 2, "3.": 3, "4.": 4,
    "5.": 5, "6.": 6, "7.": 7, "8.": 8,
    "9.": 9, "10.": 10, "11.": 11, "12.": 12,
}

CZECH_WEEKDAYS = {
    "Po": "maandag", "Út": "dinsdag", "St": "woensdag",
    "Čt": "donderdag", "Pá": "vrijdag", "So": "zaterdag", "Ne": "zondag",
}

DUTCH_MONTHS = [
    "", "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_czech_date(raw: str) -> tuple[str | None, str | None, str | None]:
    """
    Verwerkt datumstrings zoals 'Pá 10. 4. 2026' of 'So 11. 4. 2026'.
    Geeft terug: (datum_iso, tijdstip_str_or_None, dag_nl)
    """
    raw = raw.strip()
    # Patroon: "Pá 10. 4. 2026"
    m = re.match(
        r"([A-ZÁČĎÉÍŇÓŘŠŤŮÚŽÝ][a-záčďéíňóřšťůúžý])\s+(\d+)\.\s+(\d+)\.\s+(\d{4})",
        raw,
    )
    if not m:
        return None, None, None
    weekday_cz, day, month, year = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    try:
        dt = datetime(year, month, day)
    except ValueError:
        return None, None, None
    datum_iso = dt.strftime("%Y-%m-%d")
    dag_nl = f"{CZECH_WEEKDAYS.get(weekday_cz, weekday_cz)} {day} {DUTCH_MONTHS[month]}"
    return datum_iso, dag_nl


def clean_team_name(raw: str) -> tuple[str, str]:
    """
    De HTML herhaalt teamdata: 'Draci BrnoDraciDRA' → name='Draci Brno', code='DRA'
    Strategie: zoek bekende codes aan het einde, strip dan de rest.
    """
    raw = raw.strip()
    # Probeer eerst alle bekende codes
    for code in ["DRA", "KOT", "ARR", "EAG", "HRO", "SAB", "SOK", "NUC"]:
        if raw.endswith(code):
            naam_deel = raw[: -len(code)].strip()
            # Verwijder ook korte afkorting ervoor (bijv. 'Draci')
            for alias, c in TEAM_ALIASES.items():
                if c == code and naam_deel.endswith(alias):
                    return naam_deel[: -len(alias)].strip(), code
            # Fallback: gebruik het langste bekende volledige teamname
            for full_name, c in TEAM_ALIASES.items():
                if c == code and raw.startswith(full_name):
                    return full_name, code
            return naam_deel, code
    # Geen code gevonden — gebruik alias lookup
    for full_name, code in TEAM_ALIASES.items():
        if raw.startswith(full_name):
            return full_name, code
    return raw, ""


def parse_score(raw: str) -> tuple[int | None, int | None]:
    """'7:3' → (7, 3), '-:-' of '' → (None, None)"""
    raw = raw.strip()
    m = re.match(r"(\d+):(\d+)", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


class TableParser(HTMLParser):
    """
    Eenvoudige HTML-tabel parser die alle <table>-rijen verzamelt als
    lijsten van cel-teksten.
    """
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: str | None = None
        self._in_cell = False
        self._depth = 0  # voor geneste tabellen

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._current_table is None:
                self._current_table = []
                self._depth = 0
            else:
                self._depth += 1
        elif tag in ("tr",) and self._depth == 0:
            if self._current_table is not None:
                self._current_row = []
        elif tag in ("td", "th") and self._depth == 0:
            if self._current_row is not None:
                self._current_cell = ""
                self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 0 and self._current_table is not None:
                self.tables.append(self._current_table)
                self._current_table = None
                self._current_row = None
                self._current_cell = None
                self._in_cell = False
            else:
                self._depth = max(0, self._depth - 1)
        elif tag in ("tr",) and self._depth == 0:
            if self._current_row is not None and self._current_table is not None:
                if any(c.strip() for c in self._current_row):
                    self._current_table.append(self._current_row)
            self._current_row = None
        elif tag in ("td", "th") and self._depth == 0:
            if self._current_row is not None and self._current_cell is not None:
                self._current_row.append(self._current_cell.strip())
            self._current_cell = None
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell and self._current_cell is not None:
            self._current_cell += data


def extract_games_from_table(rows: list[list[str]]) -> list[dict]:
    """
    Verwerkt rijen uit de grote maandtabel.
    Kolommen: Datum | Fáze | # | Hod. | Domácí | Hosté | Výsledek | Hřiště | [Detail]
    """
    games = []
    current_datum = None
    current_dag = None

    for row in rows:
        if not row:
            continue

        # Skip header
        if row[0].strip() in ("Datum", ""):
            if len(row) >= 7 and row[0].strip() == "Datum":
                continue

        # Bepaal aantal kolommen (met of zonder detail/nadhazovači)
        if len(row) < 7:
            continue

        # Datum: kan leeg zijn als het dezelfde dag is als de vorige rij
        datum_raw = row[0].strip()
        if datum_raw:
            parsed = parse_czech_date(datum_raw)
            if parsed[0]:
                current_datum, current_dag = parsed[0], parsed[1]

        if not current_datum:
            continue

        fase = row[1].strip() if len(row) > 1 else ""
        tijdstip_raw = row[3].strip() if len(row) > 3 else ""
        thuis_raw = row[4].strip() if len(row) > 4 else ""
        uit_raw = row[5].strip() if len(row) > 5 else ""
        score_raw = row[6].strip() if len(row) > 6 else ""
        locatie_raw = row[7].strip() if len(row) > 7 else ""

        thuis_naam, thuis_code = clean_team_name(thuis_raw)
        uit_naam, uit_code = clean_team_name(uit_raw)
        score_thuis, score_uit = parse_score(score_raw)
        gespeeld = score_thuis is not None

        # Tijdstip normaliseren (bijv. "19:00")
        tijdstip = tijdstip_raw if re.match(r"\d{1,2}:\d{2}", tijdstip_raw) else None

        games.append({
            "datum": current_datum,
            "dag": current_dag,
            "tijdstip": tijdstip,
            "thuis": thuis_naam,
            "thuis_code": thuis_code,
            "uit": uit_naam,
            "uit_code": uit_code,
            "score_thuis": score_thuis,
            "score_uit": score_uit,
            "gespeeld": gespeeld,
            "fase": fase,
            "locatie": locatie_raw,
        })

    return games


def speelronde_bounds():
    """
    Geeft de meest recente speelronde (vrijdag + zaterdag + zondag).
    De Czech Extraliga speelt vr/za/zo-weekenden.

    Logica:
    - Ma t/m do → vorige week vr+za+zo
    - Vr t/m zo → huidige week vr+za+zo
    """
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()
    weekday = today.weekday()  # 0=ma … 6=zo

    # Dagen terug tot meest recente vrijdag
    days_since_friday = (weekday - 4) % 7
    friday = today - timedelta(days=days_since_friday)
    sunday = friday + timedelta(days=2)
    return friday, sunday


def format_dutch_day(datum_iso: str) -> str:
    dt = datetime.strptime(datum_iso, "%Y-%m-%d")
    days = ["maandag", "dinsdag", "woensdag", "donderdag",
            "vrijdag", "zaterdag", "zondag"]
    return f"{days[dt.weekday()]} {dt.day} {DUTCH_MONTHS[dt.month]}"


def main():
    print(f"Ophalen van {SCHEDULE_URL}...")
    html = fetch_html(SCHEDULE_URL)

    parser = TableParser()
    parser.feed(html)
    print(f"Tabellen gevonden: {len(parser.tables)}")

    # De grote maandtabel is doorgaans de tweede of derde tabel
    # We verwerken alle tabellen en bewaren de langste (meeste rijen)
    all_games = []
    for i, table in enumerate(parser.tables):
        if len(table) < 3:
            continue
        # Check of dit een wedstrijdtabel is (heeft datum + score kolommen)
        games = extract_games_from_table(table)
        if len(games) > len(all_games):
            all_games = games
            print(f"  Tabel {i}: {len(games)} wedstrijden gevonden")

    print(f"\nTotaal wedstrijden geparsed: {len(all_games)}")

    friday, sunday = speelronde_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Meest recente speelronde: {friday} (vr) t/m {sunday} (zo)")

    uitslagen = []
    programma = []

    for g in all_games:
        if not g["datum"]:
            continue
        game_date = datetime.strptime(g["datum"], "%Y-%m-%d").date()

        if g["gespeeld"] and friday <= game_date <= sunday:
            uitslagen.append(g)
        elif not g["gespeeld"] and game_date > today:
            programma.append(g)

    uitslagen.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))
    programma.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))
    programma = programma[:12]

    print(f"\nGespeelde wedstrijden in speelronde ({friday} – {sunday}):")
    for u in uitslagen:
        print(f"  {u['datum']} {u['thuis_code']} {u['score_thuis']}-{u['score_uit']} {u['uit_code']}")

    if not uitslagen:
        print("  ⚠️  Geen uitslagen gevonden voor de meest recente speelronde.")
        print("     Controleer of de pagina wedstrijdresultaten bevat.")

    print(f"\nAankomende wedstrijden (eerste {len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']} {p['thuis_code']} vs {p['uit_code']}")

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": SCHEDULE_URL,
        "speelronde": {
            "van": str(friday),
            "tot": str(sunday),
        },
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
