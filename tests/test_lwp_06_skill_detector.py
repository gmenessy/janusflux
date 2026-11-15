"""Abnahmetests für LWP-06: Vierstufiger Skill-Detektor & Decision Contract skill.detector."""

import time
from pathlib import Path

from plattform.decision.dmn import DMNEngine
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.skills.detector import SkillDetector
from plattform.skills.models import SkillBudgetSpec, SkillManifest, SkillStepSpec
from plattform.skills.registry import SkillRegistry

ALL_REF_SKILLS = {
    "dokument.schwaerzen",
    "antrag.vorpruefen",
    "dokument.extrahieren",
    "auskunft.recht",
    "eingang.routen",
}


def _build_detector() -> tuple[SkillDetector, SkillRegistry]:
    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"))
    registry.load_all(embedder_fn=gateway.embed_sync)
    dmn = DMNEngine(Path("rules"))
    return SkillDetector(registry, gateway, dmn), registry


async def test_lwp_06_stage0_explicit_and_regex_trigger_under_5ms() -> None:
    detector, _ = _build_detector()

    t0 = time.perf_counter()
    res_trigger = await detector.detect(
        text="Bitte schwärzen Sie alle Personennamen in dieser Vorlage für das Ratsinformationssystem.",
        payload={"doc_id": "123e4567-e89b-12d3-a456-426614174000", "zweck": "ratsinfo_oeffentlich"},
        allowed_skill_ids=ALL_REF_SKILLS,
        schutzklasse=2,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 5.0
    assert res_trigger.detected_skill_id == "dokument.schwaerzen"
    assert res_trigger.detection_source == "stage0_trigger"
    assert res_trigger.routing_action == "AUTO_EXECUTE"


async def test_lwp_06_missing_mandatory_fields_require_clarification() -> None:
    detector, _ = _build_detector()
    res = await detector.detect(
        text="Bitte Dokument schwärzen.",
        payload={"doc_id": "123e4567-e89b-12d3-a456-426614174000"},  # 'zweck' fehlt!
        allowed_skill_ids=ALL_REF_SKILLS,
        schutzklasse=2,
        explicit_skill_id="dokument.schwaerzen",
    )
    assert res.routing_action == "REQUIRE_CLARIFICATION"
    assert "zweck" in res.missing_fields


async def test_lwp_06_out_of_scope_query_rejected_via_kein_skill_passt() -> None:
    detector, _ = _build_detector()
    res = await detector.detect(
        text="Wie wird der Wetterbericht morgen und hast du ein gutes Kuchenrezept?",
        payload={},
        allowed_skill_ids=ALL_REF_SKILLS,
        schutzklasse=1,
    )
    assert res.routing_action == "REJECT_NO_SKILL"
    assert res.detected_skill_id is None
    assert res.dmn_rule_id == "R1_KEIN_SKILL_PASST"


async def test_lwp_06_tainted_input_on_irreversible_skill_routes_to_workbench() -> None:
    detector, registry = _build_detector()
    irreversible_skill = SkillManifest(
        skill_id="kasse.auszahlen",
        version="1.0.0",
        name="Kassenanordnung & Auszahlung",
        art="zusammengesetzt",
        beschreibung="Führt eine unwiderrufliche Kassenauszahlung nach Freigabe durch.",
        trigger_patterns=[r"\bKassenauszahlung\b"],
        stufen=[0, 3],
        wirkung="irreversible",
        max_schutzklasse=3,
        eingabe_schema={"type": "object", "required": ["betrag"]},
        ausgabe_schema={"type": "object"},
        steps=[
            SkillStepSpec(
                step_id="s1_approve",
                stufe=3,
                action="p8.approve",
                level="L2",
                begruendung="Vier-Augen-Freigabe vor Kassenanweisung",
            )
        ],
        budget=SkillBudgetSpec(llm_aufrufe=0, max_input_tokens=512, zeit_s=15),
        verantwortlich={"fachlich": "kaemmerei", "technisch": "plattform"},
    )
    registry.register(irreversible_skill, embedder_fn=detector.gateway.embed_sync)

    res = await detector.detect(
        text="Bitte Kassenauszahlung sofort ausführen.",
        payload={"betrag": 500.0},
        allowed_skill_ids=ALL_REF_SKILLS | {"kasse.auszahlen"},
        schutzklasse=2,
        input_tainted=True,
    )
    assert res.routing_action == "ROUTE_TO_WORKBENCH"
    assert res.dmn_rule_id == "R3_IRREVERSIBLE_ODER_TAINTED_WRITE_SCHUTZ"
