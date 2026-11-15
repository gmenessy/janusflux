"""Deterministisches Post-Execution-Gate (`VAL-001` bis `VAL-010`, LWP-07, Stufe 0).
Prüft jede Ausgabe eines Stufe-2-LLM-Schritts deterministisch abwärts:
- VAL-001: Keine <think>-Tags oder System-Prompt-Artefakte
- VAL-002: Keine Klartext-PII (IBAN, Steuer-ID, E-Mail) in geschützten Ausgaben
- VAL-003: Fristdatum im Text stimmt exakt mit §-31-VwVfG-Berechnung überein
- VAL-004: Geldbeträge / Korb-Zuordnung stimmen exakt mit dem DMN-Ergebnis überein
- VAL-005: Zitierte Rechtsnormen stammen ausschließlich aus den übergebenen Quell-Chunks
- VAL-006: Struktur- / Pflichtfeld-Vollständigkeit des Ausgabe-Objekts
- VAL-007: Wörtlicher Zitat-Abgleich (jedes Zitat muss exakt in der `redacted_view` stehen)
- VAL-008: Keine unautorisierte rechtsverbindliche Zusicherung ohne Freigabe
- VAL-009: Maximale Textlänge nicht überschritten
- VAL-010: Keine externen Links oder Prompt-Injection-Fragmente im Ausgabetext
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from plattform.policy_privacy.pii_detectors import detect_all_pii_spans


class PostGateValidationError(ValueError):
    """Wird geworfen, wenn eine der Regeln VAL-001 bis VAL-010 verletzt wird."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


@dataclass(frozen=True)
class PostGateReport:
    passed: bool
    failed_codes: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _normalize_ws(text: str) -> str:
    return " ".join(text.strip().split())


class PostExecutionGate:
    """Vollständiges Stufe-0-Abwärtsprüfungs-Gate (`VAL-001` bis `VAL-010`)."""

    THINK_TAG_RE = re.compile(r"</?think>|<start_of_turn>|<end_of_turn>", re.IGNORECASE)
    BETRAG_RE = re.compile(r"\b\d{1,3}(?:\.\d{3})*(?:,\d{2})\s?(?:EUR|€)\b")
    EXTERNAL_URL_RE = re.compile(r"https?://[^\s)>\"']+", re.IGNORECASE)
    UNAUTH_PROMISE_RE = re.compile(
        r"\b(hiermit\s+bewilligen\s+wir\s+rechtskräftig|unwiderruflich\s+zugesichert)\b",
        re.IGNORECASE,
    )

    def validate(
        self,
        *,
        output_data: dict[str, Any],
        source_redacted_views: list[str] | None = None,
        expected_frist_datum: str | None = None,
        expected_dmn_betrag: str | None = None,
        allowed_norms: set[str] | None = None,
        required_output_keys: list[str] | None = None,
        forbid_pii: bool = True,
        max_text_length: int = 8000,
    ) -> PostGateReport:
        failed_codes: list[str] = []
        messages: list[str] = []

        all_strings: list[str] = []
        quotes_to_verify: list[str] = []
        self._collect_strings_and_quotes(output_data, all_strings, quotes_to_verify)
        combined_text = "\n".join(all_strings)

        # VAL-001: Keine <think>-Tags oder Prompt-Delimiter
        if self.THINK_TAG_RE.search(combined_text):
            failed_codes.append("VAL-001")
            messages.append("VAL-001: Verbotene <think>- oder Turn-Tags in der Ausgabe.")

        # VAL-002: Keine Klartext-PII
        if forbid_pii:
            pii_hits = detect_all_pii_spans(combined_text)
            if pii_hits:
                failed_codes.append("VAL-002")
                messages.append(
                    f"VAL-002: Klartext-PII ({pii_hits[0].entity_type}) im Ausgabetext gefunden."
                )

        # VAL-003: Fristdatum-Konsistenz
        if expected_frist_datum:
            out_frist = output_data.get("frist_datum")
            if out_frist is not None and str(out_frist) != expected_frist_datum:
                failed_codes.append("VAL-003")
                messages.append(
                    f"VAL-003: Fristdatum '{out_frist}' weicht von §-31-VwVfG-Berechnung '{expected_frist_datum}' ab."
                )

        # VAL-004: DMN-Geldbetrags-Konsistenz
        if expected_dmn_betrag:
            mentioned_amounts = self.BETRAG_RE.findall(combined_text)
            out_betrag = output_data.get("betrag")
            if out_betrag is not None and str(out_betrag) != expected_dmn_betrag:
                failed_codes.append("VAL-004")
                messages.append(
                    f"VAL-004: Geldbetrag '{out_betrag}' weicht vom DMN-Betrag '{expected_dmn_betrag}' ab."
                )
            elif mentioned_amounts and any(a != expected_dmn_betrag for a in mentioned_amounts):
                failed_codes.append("VAL-004")
                messages.append(
                    f"VAL-004: Im Freitext genannter Betrag {mentioned_amounts} weicht vom DMN-Betrag '{expected_dmn_betrag}' ab."
                )

        # VAL-005: Norm-Fundstelle im zulässigen Bestand
        if allowed_norms is not None and "norm_fundstelle" in output_data:
            norm_val = str(output_data["norm_fundstelle"])
            if norm_val and norm_val not in allowed_norms:
                failed_codes.append("VAL-005")
                messages.append(
                    f"VAL-005: Norm-Fundstelle '{norm_val}' ist nicht in den abgerufenen Rechtsquellen {allowed_norms}."
                )

        # VAL-006: Pflichtfelder im Output
        if required_output_keys:
            missing_keys = [
                k for k in required_output_keys if k not in output_data or output_data[k] is None
            ]
            if missing_keys:
                failed_codes.append("VAL-006")
                messages.append(f"VAL-006: Fehlende Pflichtfelder im Ergebnis: {missing_keys}")

        # VAL-007: Wörtlicher Zitat-Abgleich auf der redacted_view
        if source_redacted_views is not None and quotes_to_verify:
            norm_sources = [_normalize_ws(src) for src in source_redacted_views]
            for quote in quotes_to_verify:
                norm_q = _normalize_ws(quote)
                if not norm_q:
                    failed_codes.append("VAL-007")
                    messages.append("VAL-007: Leeres Zitat trotz Zitatpflicht.")
                    break
                if not any(norm_q in src for src in norm_sources):
                    failed_codes.append("VAL-007")
                    messages.append(
                        f"VAL-007: Halluziniertes oder verändertes Zitat '{quote}' kommt nicht wörtlich in der redacted_view vor."
                    )
                    break

        # VAL-008: Keine unautorisierte rechtskräftige Zusage
        if self.UNAUTH_PROMISE_RE.search(combined_text):
            failed_codes.append("VAL-008")
            messages.append("VAL-008: Unautorisierte rechtsverbindliche Zusicherung erkannt.")

        # VAL-009: Maximale Länge
        if len(combined_text) > max_text_length:
            failed_codes.append("VAL-009")
            messages.append(
                f"VAL-009: Ausgabetext überschreitet Maximallänge ({len(combined_text)} > {max_text_length})."
            )

        # VAL-010: Keine externen URLs / Exfiltrationslinks
        if self.EXTERNAL_URL_RE.search(combined_text):
            failed_codes.append("VAL-010")
            messages.append("VAL-010: Unerlaubte externe URL im Ausgabetext gefunden.")

        return PostGateReport(
            passed=(len(failed_codes) == 0),
            failed_codes=failed_codes,
            messages=messages,
        )

    def enforce(self, **kwargs: Any) -> PostGateReport:
        """Wirft `PostGateValidationError`, sobald eine der Regeln VAL-001..VAL-010 fehlschlägt."""
        report = self.validate(**kwargs)
        if not report.passed:
            raise PostGateValidationError(report.failed_codes[0], "; ".join(report.messages))
        return report

    @classmethod
    def _collect_strings_and_quotes(
        cls, obj: Any, strings_out: list[str], quotes_out: list[str]
    ) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("zitat", "zitat_wortlaut") and isinstance(v, str):
                    quotes_out.append(v)
                    strings_out.append(v)
                elif isinstance(v, str):
                    strings_out.append(v)
                else:
                    cls._collect_strings_and_quotes(v, strings_out, quotes_out)
        elif isinstance(obj, list):
            for item in obj:
                cls._collect_strings_and_quotes(item, strings_out, quotes_out)
