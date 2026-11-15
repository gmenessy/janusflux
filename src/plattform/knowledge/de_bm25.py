"""Deutscher BM25-Vorverarbeiter mit Kompositazerlegung & Snowball-Stemmer (LWP-08).
Zerlegt deutsche Verwaltungskomposita (z. B. `Wohngeldantragsformular` ->
`wohngeld`, `antrag`, `formular`) inkl. Fugen-s-Tilgung und deutschem Suffix-Stemming,
damit BM25 auch Teilbegriffe zuverlässig matcht.
"""

from __future__ import annotations

import math
import re

GERMAN_COMPOUND_ROOTS: tuple[str, ...] = (
    "wohngeld",
    "elterngeld",
    "buergergeld",
    "bürgergeld",
    "antrag",
    "formular",
    "bescheid",
    "frist",
    "berechnung",
    "sondernutzung",
    "gebuehr",
    "gebühr",
    "satzung",
    "einkommen",
    "nachweis",
    "miet",
    "miete",
    "vertrag",
    "bescheinigung",
    "schwärzung",
    "schwaerzung",
    "akte",
    "einsicht",
    "auskunft",
    "widerspruch",
    "verwaltung",
    "verfahren",
    "werktag",
    "feiertag",
    "sonntag",
    "behörde",
    "behoerde",
)

STOPWORDS_DE: set[str] = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einer",
    "einem",
    "einen",
    "und",
    "oder",
    "in",
    "im",
    "auf",
    "für",
    "von",
    "mit",
    "nach",
    "bei",
    "zu",
    "zum",
    "zur",
    "ist",
    "sind",
    "wird",
    "werden",
}


def stem_german_word(word: str) -> str:
    """Leichtgewichtiger deutscher Snowball-artiger Suffix-Stemmer."""
    w = word.lower().strip()
    w = w.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    for suffix in ("ungen", "ung", "keiten", "keit", "heiten", "heit", "ern", "em", "en", "er", "es", "e", "s"):
        if len(w) - len(suffix) >= 4 and w.endswith(suffix):
            w = w[: -len(suffix)]
            break
    return w


def decompose_german_compound(token: str) -> list[str]:
    """Zerlegt ein deutsches Kompositum deterministisch in seine Wortstämme."""
    lower = token.lower()
    parts: list[str] = [lower]
    for root in GERMAN_COMPOUND_ROOTS:
        if root in lower and root != lower:
            parts.append(root)
    # Zusätzlich gierige Zerlegung mit Fugen-s / Fugen-n
    remaining = lower
    while len(remaining) >= 6:
        matched_prefix = None
        for root in sorted(GERMAN_COMPOUND_ROOTS, key=len, reverse=True):
            if remaining.startswith(root):
                matched_prefix = root
                break
        if not matched_prefix:
            break
        if matched_prefix not in parts:
            parts.append(matched_prefix)
        remaining = remaining[len(matched_prefix) :]
        if remaining.startswith(("s", "n", "en", "es")) and len(remaining) > 4:
            for fug in ("en", "es", "s", "n"):
                if remaining.startswith(fug) and any(
                    remaining[len(fug) :].startswith(r) for r in GERMAN_COMPOUND_ROOTS
                ):
                    remaining = remaining[len(fug) :]
                    break
    if len(remaining) >= 4 and remaining not in parts:
        parts.append(remaining)
    return parts


def tokenize_de_bm25(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-zÄÖÜäöüß0-9§]+", text.lower())
    expanded: list[str] = []
    for tok in raw_tokens:
        if tok in STOPWORDS_DE:
            continue
        for sub in decompose_german_compound(tok):
            if sub not in STOPWORDS_DE:
                expanded.append(sub)
                stemmed = stem_german_word(sub)
                if stemmed != sub:
                    expanded.append(stemmed)
    return expanded


class GermanBM25Index:
    """BM25-Index mit deutscher Kompositazerlegung für Stufe-0-Lexikalsuche."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_tokens: dict[str, list[str]] = {}
        self._df: dict[str, int] = {}
        self._avgdl: float = 1.0

    def index_documents(self, docs: dict[str, str]) -> None:
        self._doc_tokens.clear()
        self._df.clear()
        total_len = 0
        for doc_id, text in docs.items():
            tokens = tokenize_de_bm25(text)
            self._doc_tokens[doc_id] = tokens
            total_len += len(tokens)
            for term in set(tokens):
                self._df[term] = self._df.get(term, 0) + 1
        n_docs = len(docs) or 1
        self._avgdl = (total_len / n_docs) if total_len > 0 else 1.0

    def search(self, query: str, allowed_doc_ids: set[str] | None = None, top_k: int = 10) -> list[tuple[str, float]]:
        q_terms = tokenize_de_bm25(query)
        n_docs = len(self._doc_tokens) or 1
        scores: list[tuple[str, float]] = []

        for doc_id, d_tokens in self._doc_tokens.items():
            if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
                continue
            dl = len(d_tokens) or 1
            score = 0.0
            for qt in q_terms:
                tf = d_tokens.count(qt)
                if tf == 0:
                    continue
                df = self._df.get(qt, 0)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / self._avgdl))
                score += idf * ((tf * (self.k1 + 1.0)) / denom)
            if score > 0.0:
                scores.append((doc_id, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:top_k]
