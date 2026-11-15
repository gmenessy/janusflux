"""Abnahmetests für LWP-05: Skill-Registry, Statischer Skill-Ketten-Linter & Referenz-Skills."""

from pathlib import Path

import pytest

from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.skills.models import SkillBudgetSpec, SkillManifest, SkillStepSpec
from plattform.skills.registry import SkillChainLintError, SkillRegistry


def test_lwp_05_loads_all_five_reference_skills() -> None:
    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"))
    registry.load_all(embedder_fn=gateway.embed_sync)

    expected_ids = {
        "dokument.schwaerzen",
        "antrag.vorpruefen",
        "dokument.extrahieren",
        "auskunft.recht",
        "eingang.routen",
    }
    allowed = registry.list_allowed(expected_ids, schutzklasse=2)
    assert {s.skill_id for s in allowed} == expected_ids


def test_lwp_05_rejects_stage2_without_downstream_stage0_gate() -> None:
    bad_manifest = SkillManifest(
        skill_id="test.unguarded",
        version="1.0.0",
        name="Unguarded LLM Skill",
        art="zusammengesetzt",
        beschreibung="Ungültiger Skill ohne Abwärtsprüfung nach Stufe 2.",
        stufen=[0, 2],
        wirkung="read",
        eingabe_schema={"type": "object", "required": ["q"]},
        ausgabe_schema={"type": "object", "required": ["a"]},
        steps=[
            SkillStepSpec(
                step_id="s1_llm",
                stufe=2,
                action="p5.generate",
                begruendung="LLM-Aufruf ohne nachfolgendes Post-Gate",
            )
        ],
        budget=SkillBudgetSpec(llm_aufrufe=1, max_input_tokens=512, zeit_s=10),
        verantwortlich={"fachlich": "test", "technisch": "test"},
    )
    with pytest.raises(SkillChainLintError, match="Abwärtsprüfung"):
        SkillRegistry._lint_manifest(bad_manifest)


def test_lwp_05_rejects_irreversible_skill_without_stage3_approval() -> None:
    bad_irreversible = SkillManifest(
        skill_id="test.irreversible",
        version="1.0.0",
        name="Irreversibel ohne Mensch",
        art="atomar",
        beschreibung="Ungültiger irreversibler Skill ohne Stufe 3.",
        stufen=[0],
        wirkung="irreversible",
        eingabe_schema={"type": "object"},
        ausgabe_schema={"type": "object"},
        steps=[
            SkillStepSpec(
                step_id="s1_write",
                stufe=0,
                action="p7.write_external",
                begruendung="Direkter Schreibzugriff ohne Freigabe",
            )
        ],
        budget=SkillBudgetSpec(llm_aufrufe=0, max_input_tokens=512, zeit_s=10),
        verantwortlich={"fachlich": "test", "technisch": "test"},
    )
    with pytest.raises(SkillChainLintError, match="Stufe-3-Freigabeschritt"):
        SkillRegistry._lint_manifest(bad_irreversible)
