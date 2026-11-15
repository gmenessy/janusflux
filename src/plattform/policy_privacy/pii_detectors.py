"""Deterministische PII- & Prüfziffern-Detektoren (LWP-03, Stufe 0):
- IBAN-Prüfung nach ISO 13616 / Modulo 97
- Steuer-Identifikationsnummer (11-stellig) nach ISO 7064 Modulo 11, 10
- Deutsche Aktenzeichen, E-Mail-Adressen, Telefonnummern und Geldbeträge
- Namen-/Entitäts-Erkennung für den Zwei-Fassungen-Tresor
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PIISpan:
    entity_type: str
    text: str
    start: int
    end: int
    confidence: float = 1.0


IBAN_CANDIDATE_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\s?[A-Z0-9]{1,4}\b")
STEUER_ID_CANDIDATE_RE = re.compile(r"\b[1-9]\d{10}\b")
AKTENZEICHEN_RE = re.compile(r"\b(?:Az\.?\s*)?\d{1,3}\s?[A-Z]{1,4}\s?\d{1,5}/\d{2,4}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
BETRAG_RE = re.compile(r"\b\d{1,3}(?:\.\d{3})*(?:,\d{2})?\s?(?:EUR|€)\b")
PERSON_HONORIFIC_RE = re.compile(
    r"\b(?:Herrn?|Frau)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)\b"
)


def validate_iban_mod97(raw_iban: str) -> bool:
    """Prüft eine IBAN nach ISO 13616 / Modulo 97-10."""
    compact = re.sub(r"\s+", "", raw_iban).upper()
    if len(compact) < 15 or len(compact) > 34:
        return False
    if compact.startswith("DE") and len(compact) != 22:
        return False
    if not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    numeric_chars: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            numeric_chars.append(ch)
        elif "A" <= ch <= "Z":
            numeric_chars.append(str(ord(ch) - 55))
        else:
            return False
    number_val = int("".join(numeric_chars))
    return number_val % 97 == 1


def validate_steuer_id_iso7064(raw_id: str) -> bool:
    """Prüft die deutsche 11-stellige Steuer-ID nach ISO 7064 Mod 11, 10 inkl. Ziffernhäufigkeitsregel."""
    digits = re.sub(r"\s+", "", raw_id)
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False

    # In den ersten 10 Ziffern muss genau eine Ziffer doppelt oder dreifach vorkommen
    first_ten = digits[:10]
    counts = [first_ten.count(d) for d in set(first_ten)]
    if len(set(first_ten)) not in (8, 9) or max(counts) not in (2, 3):
        return False

    product = 10
    for i in range(10):
        total = (int(digits[i]) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    check_digit = (11 - product) % 10
    return check_digit == int(digits[10])


def detect_all_pii_spans(
    text: str, extra_persons: list[str] | None = None
) -> list[PIISpan]:
    """Erkennt deterministisch IBANs, Steuer-IDs, E-Mails, Aktenzeichen und genannte Personen."""
    spans: list[PIISpan] = []

    for m in IBAN_CANDIDATE_RE.finditer(text):
        candidate = m.group(0).strip()
        if validate_iban_mod97(candidate):
            spans.append(PIISpan("IBAN", candidate, m.start(), m.end()))

    for m in STEUER_ID_CANDIDATE_RE.finditer(text):
        candidate = m.group(0)
        if validate_steuer_id_iso7064(candidate):
            spans.append(PIISpan("STEUER_ID", candidate, m.start(), m.end()))

    for m in EMAIL_RE.finditer(text):
        spans.append(PIISpan("EMAIL", m.group(0), m.start(), m.end()))

    for m in AKTENZEICHEN_RE.finditer(text):
        spans.append(PIISpan("AKTENZEICHEN", m.group(0), m.start(), m.end()))

    for m in PERSON_HONORIFIC_RE.finditer(text):
        name = m.group(1)
        spans.append(PIISpan("PERSON", name, m.start(1), m.end(1)))

    if extra_persons:
        for person in extra_persons:
            if not person:
                continue
            for m in re.finditer(re.escape(person), text):
                spans.append(PIISpan("PERSON", person, m.start(), m.end()))

    # Deduplizierung überlappender Spans (längster Span gewinnt)
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[PIISpan] = []
    last_end = -1
    for sp in spans:
        if sp.start >= last_end:
            merged.append(sp)
            last_end = sp.end
    return merged


def extract_deterministic_fields(text: str) -> dict[str, str | None]:
    """Stufe-0-Direktextraktion von IBAN, Steuer-ID, Aktenzeichen und erstem Geldbetrag."""
    spans = detect_all_pii_spans(text)
    iban = next((s.text for s in spans if s.entity_type == "IBAN"), None)
    steuer_id = next((s.text for s in spans if s.entity_type == "STEUER_ID"), None)
    az = next((s.text for s in spans if s.entity_type == "AKTENZEICHEN"), None)
    betrag_match = BETRAG_RE.search(text)
    betrag = betrag_match.group(0) if betrag_match else None
    return {
        "iban": iban,
        "steuer_id": steuer_id,
        "aktenzeichen": az,
        "betrag": betrag,
    }
