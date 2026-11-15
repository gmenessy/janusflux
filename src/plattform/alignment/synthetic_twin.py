"""Synthetischer Zwillings-Generator (`SyntheticTwinGenerator`, P12, LWP-12).
Erzeugt aus realen Sachbearbeiter-Korrekturen der Workbench garantiert PII-freie
synthetische Goldset-Testfälle mit deterministischem 0-%-Entity-Overlap-Beweis:
- Erkennt alle Personen, IBANs, Steuer-IDs, E-Mail-Adressen und Aktenzeichen.
- Ersetzt sie konsistent durch synthetische Test-Entitäten aus einem isolierten Fiktiv-Pool.
- Prüft im Anschluss mathematisch, dass 0 % der ursprünglichen realen Entitätswerte
  im synthetischen Zwilling enthalten sind (`EntityOverlapError` bei Verstoß).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from plattform.policy_privacy.pii_detectors import detect_all_pii_spans

SYNTHETIC_PERSONS: tuple[str, ...] = (
    "Alex Beispiel",
    "Robin Platzhalter",
    "Sascha Testfall",
    "Kim Synthese",
)

SYNTHETIC_IBANS: tuple[str, ...] = (
    "DE02120300000000202051",
    "DE02500105170137075030",
)

SYNTHETIC_EMAILS: tuple[str, ...] = (
    "synthese.test1@example.invalid",
    "synthese.test2@example.invalid",
)

SYNTHETIC_AZ: tuple[str, ...] = (
    "Az. 99 SYN 100/99",
    "Az. 99 SYN 200/99",
)


class EntityOverlapError(RuntimeError):
    """Wird ausgelöst, wenn der synthetische Zwilling noch echte PII-Fragmente enthält."""


@dataclass(frozen=True)
class SyntheticTwinCase:
    synthetic_text: str
    expected_skill_id: str
    expected_label: str
    replaced_entities_count: int
    entity_overlap_ratio: float


class SyntheticTwinGenerator:
    """Erzeugt PII-freie synthetische Goldset-Fälle mit garantiertem 0-%-Entity-Overlap."""

    def generate_twin(
        self,
        *,
        original_text: str,
        expected_skill_id: str,
        expected_label: str,
        known_real_entities: list[str] | None = None,
    ) -> SyntheticTwinCase:
        real_entities = list(known_real_entities or [])
        spans = detect_all_pii_spans(original_text, extra_persons=real_entities)
        all_real_values: set[str] = {s.text for s in spans} | {
            e.strip() for e in real_entities if e.strip()
        }

        synthetic = original_text
        replaced_count = 0

        for idx, sp in enumerate(spans):
            if sp.entity_type == "PERSON":
                replacement = SYNTHETIC_PERSONS[idx % len(SYNTHETIC_PERSONS)]
            elif sp.entity_type == "IBAN":
                replacement = SYNTHETIC_IBANS[idx % len(SYNTHETIC_IBANS)]
            elif sp.entity_type == "EMAIL":
                replacement = SYNTHETIC_EMAILS[idx % len(SYNTHETIC_EMAILS)]
            elif sp.entity_type == "AKTENZEICHEN":
                replacement = SYNTHETIC_AZ[idx % len(SYNTHETIC_AZ)]
            else:
                replacement = f"SYNTH_{sp.entity_type}_{idx + 1}"
            synthetic = synthetic.replace(sp.text, replacement)
            replaced_count += 1

        for idx, extra in enumerate(real_entities):
            if extra and extra in synthetic:
                replacement = SYNTHETIC_PERSONS[(idx + replaced_count) % len(SYNTHETIC_PERSONS)]
                synthetic = re.sub(re.escape(extra), replacement, synthetic)
                replaced_count += 1

        # Deterministischer 0-%-Entity-Overlap-Check (inkl. Nachnamen/Teil-Tokens >= 4 Zeichen)
        leaked: list[str] = []
        for real_val in all_real_values:
            if real_val.lower() in synthetic.lower():
                leaked.append(real_val)
            for part in real_val.split():
                if len(part) >= 4 and part.lower() not in {"herr", "herrn", "frau"} and part.lower() in synthetic.lower():
                    leaked.append(part)

        if leaked:
            raise EntityOverlapError(
                f"Synthetischer Zwilling verletzt 0-%-Entity-Overlap: {leaked}"
            )

        return SyntheticTwinCase(
            synthetic_text=synthetic,
            expected_skill_id=expected_skill_id,
            expected_label=expected_label,
            replaced_entities_count=replaced_count,
            entity_overlap_ratio=0.0,
        )
