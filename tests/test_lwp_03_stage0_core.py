"""Abnahmetests für LWP-03: Deterministischer Kern (Stufe 0) – DMN-Engine, §-31-VwVfG-Fristen & Zwei-Fassungen-Privacy."""

from datetime import date

import pytest

from plattform.decision.dmn import DMNCondition, DMNEngine, DMNOverlapError, DMNRule, DMNTableSpec
from plattform.decision.fristen import berechne_frist_vwvfg
from plattform.policy_privacy.pii_detectors import (
    validate_iban_mod97,
    validate_steuer_id_iso7064,
)
from plattform.policy_privacy.redaction_vault import RedactionVault


def test_lwp_03_dmn_rejects_overlapping_unique_rules() -> None:
    engine = DMNEngine()
    overlapping_spec = DMNTableSpec(
        table_id="test_unique_overlap",
        version=1,
        hit_policy="UNIQUE",
        owner="test",
        rechtsgrundlage="Test",
        input_schema={"alter": "integer"},
        output_schema={"tarif": "string"},
        rules=[
            DMNRule(
                rule_id="R1",
                conditions=[DMNCondition(field="alter", operator=">=", value=18)],
                output={"tarif": "ERWACHSEN"},
                begruendung_template="Ab 18",
            ),
            DMNRule(
                rule_id="R2",
                conditions=[DMNCondition(field="alter", operator=">=", value=25)],
                output={"tarif": "VOLL"},
                begruendung_template="Ab 25 überlappt mit >= 18",
            ),
        ],
    )
    with pytest.raises(DMNOverlapError):
        engine.register_table(overlapping_spec)


def test_lwp_03_dmn_evaluates_builtin_tables() -> None:
    engine = DMNEngine()
    res = engine.evaluate(
        "antrag_vollstaendigkeit",
        {
            "pflichtfelder_vollstaendig": True,
            "einkommensnachweis_pfad": "yes",
            "mietnachweis_pfad": "yes",
        },
    )
    assert res.output["korb"] == "GRUEN"
    assert res.matched_rule_ids == ["R4_VOLLSTAENDIG_GRUEN"]


def test_lwp_03_fristen_vwvfg_sunday_and_holiday_shift() -> None:
    # Sonntag, 15.11.2026 -> nächster Werktag Montag, 16.11.2026
    res_sunday = berechne_frist_vwvfg(date(2026, 11, 1), tage=14, bundesland="NW")
    assert res_sunday.rohes_fristende == date(2026, 11, 15)
    assert res_sunday.effektives_fristende == date(2026, 11, 16)
    assert res_sunday.verschoben_wegen_feiertag_oder_wochenende is True

    # 1. Mai 2026 (Freitag, Tag der Arbeit) -> verschiebt über Sa/So auf Montag, 04.05.2026
    res_holiday = berechne_frist_vwvfg(date(2026, 4, 17), tage=14, bundesland="NW")
    assert res_holiday.rohes_fristende == date(2026, 5, 1)
    assert res_holiday.effektives_fristende == date(2026, 5, 4)
    assert res_holiday.verschoben_wegen_feiertag_oder_wochenende is True


def test_lwp_03_iban_and_steuer_id_checksums() -> None:
    assert validate_iban_mod97("DE89 3704 0044 0532 0130 00") is True
    assert validate_iban_mod97("DE00 3704 0044 0532 0130 00") is False

    # Valide 11-stellige Steuer-ID nach ISO 7064 Mod 11, 10: 86095742719
    assert validate_steuer_id_iso7064("86095742719") is True
    assert validate_steuer_id_iso7064("86095742710") is False


def test_lwp_03_redaction_vault_consistent_two_view_placeholders() -> None:
    vault = RedactionVault(known_persons=["Max Mustermann", "Erika Musterfrau"])
    antrag = (
        "Antragsteller Herr Max Mustermann (IBAN DE89370400440532013000) "
        "beantragt Wohngeld gemeinsam mit Frau Erika Musterfrau."
    )
    chunk = "Laut Mietvertrag zahlt Max Mustermann monatlich 650,00 EUR Kaltmiete."

    redacted_antrag = vault.redact_text(antrag)
    redacted_chunk = vault.redact_text(chunk)

    assert "Max Mustermann" not in redacted_antrag
    assert "Max Mustermann" not in redacted_chunk
    assert "<PERSON_1>" in redacted_antrag
    assert "<PERSON_1>" in redacted_chunk
    assert "<PERSON_2>" in redacted_antrag
    assert "<IBAN_1>" in redacted_antrag

    rehydrated = vault.rehydrate_text(redacted_chunk)
    assert "Max Mustermann" in rehydrated
