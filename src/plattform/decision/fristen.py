"""Deterministische Fristenberechnung nach §§ 31, 41 VwVfG i.V.m. §§ 187–193 BGB (LWP-03).
Berücksichtigt Wochenenden (Samstag/Sonntag) sowie gesetzliche bundes- und landesspezifische
Feiertage (inkl. Gaußscher Osterformel für bewegliche Feiertage).
Fällt das Fristende auf einen Samstag, Sonntag oder gesetzlichen Feiertag,
verschiebt sich der Ablauf nach § 31 Abs. 3 Satz 1 VwVfG auf den nächstfolgenden Werktag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class FristErgebnis:
    ereignis_datum: date
    fristbeginn: date
    rohes_fristende: date
    effektives_fristende: date
    verschoben_wegen_feiertag_oder_wochenende: bool
    bundesland: str
    rechtsgrundlage: str = "§ 31 Abs. 1 und Abs. 3 VwVfG i.V.m. §§ 187 Abs. 1, 188, 193 BGB"


def berechne_ostersonntag(year: int) -> date:
    """Gaußsche/Lichtenbergsche Osterformel für den gregorianischen Kalender."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_val = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_val) // 451
    month = (h + l_val - 7 * m + 114) // 31
    day = ((h + l_val - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def gesetzliche_feiertage(year: int, bundesland: str = "NW") -> set[date]:
    """Liefert alle gesetzlichen Feiertage für das gegebene Jahr und Bundesland."""
    ostern = berechne_ostersonntag(year)
    holidays: set[date] = {
        date(year, 1, 1),  # Neujahr
        ostern - timedelta(days=2),  # Karfreitag
        ostern + timedelta(days=1),  # Ostermontag
        date(year, 5, 1),  # Tag der Arbeit
        ostern + timedelta(days=39),  # Christi Himmelfahrt
        ostern + timedelta(days=50),  # Pfingstmontag
        date(year, 10, 3),  # Tag der Deutschen Einheit
        date(year, 12, 25),  # 1. Weihnachtstag
        date(year, 12, 26),  # 2. Weihnachtstag
    }
    bl = bundesland.upper()
    if bl in {"BW", "BY", "ST"}:
        holidays.add(date(year, 1, 6))  # Heilige Drei Könige
    if bl in {"BE", "MV"}:
        holidays.add(date(year, 3, 8))  # Internationaler Frauentag
    if bl in {"BW", "BY", "HE", "NW", "RP", "SL"}:
        holidays.add(ostern + timedelta(days=60))  # Fronleichnam
    if bl in {"BY", "SL"}:
        holidays.add(date(year, 8, 15))  # Mariä Himmelfahrt
    if bl in {"TH"}:
        holidays.add(date(year, 9, 20))  # Weltkindertag
    if bl in {"BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"}:
        holidays.add(date(year, 10, 31))  # Reformationstag
    if bl in {"BW", "BY", "NW", "RP", "SL"}:
        holidays.add(date(year, 11, 1))  # Allerheiligen
    return holidays


def ist_werktag(d: date, bundesland: str = "NW") -> bool:
    """Prüft, ob ein Tag weder Samstag (5) noch Sonntag (6) noch gesetzlicher Feiertag ist."""
    if d.weekday() >= 5:
        return False
    return d not in gesetzliche_feiertage(d.year, bundesland)


def naechster_werktag(d: date, bundesland: str = "NW") -> date:
    current = d
    while not ist_werktag(current, bundesland):
        current += timedelta(days=1)
    return current


def berechne_frist_vwvfg(
    ereignis_datum: date,
    tage: int = 0,
    monate: int = 0,
    bundesland: str = "NW",
) -> FristErgebnis:
    """Berechnet eine behördliche Ereignisfrist nach § 31 VwVfG i.V.m. § 187 Abs. 1 BGB."""
    fristbeginn = ereignis_datum + timedelta(days=1)
    if monate > 0:
        year = ereignis_datum.year + (ereignis_datum.month + monate - 1) // 12
        month = (ereignis_datum.month + monate - 1) % 12 + 1
        # Letzter Tag des Zielmonats beachten (z. B. 31.01. + 1 Monat -> 28./29.02.)
        next_month_first = (
            date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        )
        max_day = (next_month_first - timedelta(days=1)).day
        day = min(ereignis_datum.day, max_day)
        rohes_ende = date(year, month, day) + timedelta(days=tage)
    else:
        rohes_ende = ereignis_datum + timedelta(days=tage)

    effektiv = naechster_werktag(rohes_ende, bundesland=bundesland)
    return FristErgebnis(
        ereignis_datum=ereignis_datum,
        fristbeginn=fristbeginn,
        rohes_fristende=rohes_ende,
        effektives_fristende=effektiv,
        verschoben_wegen_feiertag_oder_wochenende=(effektiv != rohes_ende),
        bundesland=bundesland,
    )
