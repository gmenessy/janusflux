"""Abnahmetests für LWP-07: Skill-Executor, Durable RunContext, CaMeL-Taint-Tracking & Post-Execution-Gate."""

from pathlib import Path
from typing import Any

import pytest

from plattform.decision.dmn import DMNEngine
from plattform.decision.post_gate import PostExecutionGate, PostGateValidationError
from plattform.evidence.audit import AuditWriter
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.runtime.context import RunContext
from plattform.skills.detector import SkillDetector
from plattform.skills.executor import SkillExecutor, SkillSecurityViolationError
from plattform.skills.models import SkillBudgetSpec, SkillManifest, SkillStepSpec
from plattform.skills.registry import SkillRegistry


async def test_lwp_07_detect_and_run_skill_with_two_phase_journal() -> None:
    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"))
    registry.load_all(embedder_fn=gateway.embed_sync)
    dmn = DMNEngine(Path("rules"))
    detector = SkillDetector(registry, gateway, dmn)
    post_gate = PostExecutionGate()

    async def h_extract_det(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"det_iban": "DE89370400440532013000"}

    async def h_extract_llm(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {
            "felder": {
                "kaltmiete": {
                    "wert": "650,00 EUR",
                    "zitat": "Die monatliche Kaltmiete beträgt 650,00 EUR.",
                    "konfidenz_pfad": "auto",
                }
            }
        }

    async def h_verify_quotes(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        post_gate.enforce(
            output_data=state.get("felder", {}),
            source_redacted_views=[state["dokument_text"]],
            forbid_pii=False,
        )
        return {"quotes_verified": True}

    executor = SkillExecutor(
        registry,
        {
            "p2.extract_deterministic_fields": h_extract_det,
            "p5.extract_remaining_fields": h_extract_llm,
            "p3.verify_exact_quotes": h_verify_quotes,
        },
    )
    audit = AuditWriter()
    ctx = RunContext(
        run_id="run-lwp-07",
        tenant_id="stadt-musterhausen",
        schutzklasse=2,
        allowed_skills={"dokument.extrahieren"},
        skill_detector=detector,
        skill_executor=executor,
        audit_writer=audit,
    )
    ctx.mark_tainted("dokument_text")

    combined = await ctx.detect_and_run_skill(
        text="Bitte extrahiere die Kaltmiete aus diesem Mietvertrag.",
        payload={
            "dokument_text": "Mietvertrag § 3: Die monatliche Kaltmiete beträgt 650,00 EUR.",
            "zielfelder": ["kaltmiete"],
        },
    )
    assert combined["detection"]["detected_skill_id"] == "dokument.extrahieren"
    assert combined["execution"] is not None
    assert combined["execution"]["status"] == "COMPLETED"
    assert combined["execution"]["llm_calls_used"] == 1
    assert len(ctx.journal) == 6  # 3 Schritte * (STARTED + COMPLETED)
    assert await audit.verify_chain("stadt-musterhausen") is True


def test_lwp_07_post_gate_blocks_hallucinated_quote_and_wrong_dmn_amount() -> None:
    gate = PostExecutionGate()
    redacted_source = "Gemäß § 31 Abs. 3 VwVfG endet die Frist mit dem Ablauf des nächsten Werktages."

    # VAL-007: Halluziniertes Zitat wird sofort gestoppt
    with pytest.raises(PostGateValidationError, match="VAL-007"):
        gate.enforce(
            output_data={
                "antwort_satz": "Die Frist verlängert sich.",
                "zitat_wortlaut": "Die Frist verlängert sich automatisch um zwei Wochen.",
            },
            source_redacted_views=[redacted_source],
        )

    # VAL-004: Abweichender DMN-Geldbetrag wird sofort gestoppt
    with pytest.raises(PostGateValidationError, match="VAL-004"):
        gate.enforce(
            output_data={
                "text": "Es wird ein Wohngeld in Höhe von 999,00 EUR ausgezahlt.",
                "betrag": "999,00 EUR",
            },
            expected_dmn_betrag="320,00 EUR",
        )


async def test_lwp_07_budget_exceeded_raises_security_violation() -> None:
    registry = SkillRegistry(Path("skills"))
    over_budget_manifest = SkillManifest(
        skill_id="test.budget_violation",
        version="1.0.0",
        name="Budget Test",
        art="zusammengesetzt",
        beschreibung="Testet Laufzeit-Budgetprüfung.",
        stufen=[0, 2],
        wirkung="read",
        eingabe_schema={"type": "object"},
        ausgabe_schema={"type": "object"},
        steps=[
            SkillStepSpec(
                step_id="s1_llm",
                stufe=2,
                action="noop",
                begruendung="LLM-Aufruf trotz 0 Budget zur Laufzeit",
            ),
            SkillStepSpec(
                step_id="s2_gate",
                stufe=0,
                action="noop",
                begruendung="Nachfolgendes Stufe-0-Gate",
            ),
        ],
        budget=SkillBudgetSpec(llm_aufrufe=0, max_input_tokens=512, zeit_s=10),
        verantwortlich={"fachlich": "t", "technisch": "t"},
    )
    # Umgehe Linter gezielt, um die Laufzeit-Schranke im SkillExecutor zu testen
    registry._skills["test.budget_violation"] = over_budget_manifest

    async def noop_handler(ctx: Any, **_: Any) -> dict[str, Any]:
        return {}

    executor = SkillExecutor(registry, {"noop": noop_handler})
    ctx = RunContext(
        run_id="r-budget",
        tenant_id="stadt-musterhausen",
        schutzklasse=1,
        allowed_skills={"test.budget_violation"},
        skill_detector=None,
        skill_executor=executor,
        audit_writer=AuditWriter(),
    )
    with pytest.raises(SkillSecurityViolationError, match="Budget-Überschreitung"):
        await executor.execute(ctx, "test.budget_violation", inputs={})
