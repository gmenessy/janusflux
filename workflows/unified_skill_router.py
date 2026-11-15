"""End-to-End Unified Skill Router Workflow (LWP-11).
Verarbeitet eingehende Vorgänge über den vierstufigen `SkillDetector` und `SkillExecutor`
unter strikter Einhaltung der Stufen- und Begründungspflicht (`stufe=`, `begruendung=`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from plattform.decision.dmn import DMNEngine
from plattform.evidence.audit import AuditWriter
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.runtime.context import RunContext
from plattform.skills.builtin_handlers import build_builtin_action_handlers
from plattform.skills.detector import SkillDetector
from plattform.skills.executor import SkillExecutor
from plattform.skills.registry import SkillRegistry
from plattform.workbench.routes import WorkbenchApprovalStore

ALL_REFERENCE_SKILLS: set[str] = {
    "dokument.schwaerzen",
    "antrag.vorpruefen",
    "dokument.extrahieren",
    "auskunft.recht",
    "eingang.routen",
}


async def run_unified_skill_workflow(
    ctx: RunContext,
    text: str,
    payload: dict[str, Any],
    explicit_skill_id: str | None = None,
) -> dict[str, Any]:
    """Haupt-Workflow-Einstiegspunkt: Erkennt den passenden Skill und führt ihn kontrolliert aus."""
    return await ctx.detect_and_run_skill(
        text=text,
        payload=payload,
        explicit_skill_id=explicit_skill_id,
        stufe=1,
        begruendung="Vierstufige Skill-Detektion (Stufe 0/0.5/1/0) und kontrollierte Skill-Ausführung",
    )


def create_default_runtime_context(
    *,
    tenant_id: str = "stadt-musterhausen",
    schutzklasse: int = 2,
    allowed_skills: set[str] | None = None,
    input_tainted: bool = True,
) -> tuple[RunContext, WorkbenchApprovalStore, AuditWriter]:
    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"))
    registry.load_all(embedder_fn=gateway.embed_sync)
    dmn = DMNEngine(Path("rules"))
    approval_store = WorkbenchApprovalStore()
    audit_writer = AuditWriter()

    handlers = build_builtin_action_handlers(
        dmn_engine=dmn,
        gateway=gateway,
        approval_store=approval_store,
    )
    detector = SkillDetector(registry=registry, media_pipe_gateway=gateway, dmn_engine=dmn)
    executor = SkillExecutor(registry=registry, action_handlers=handlers)

    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        schutzklasse=schutzklasse,
        allowed_skills=allowed_skills or set(ALL_REFERENCE_SKILLS),
        skill_detector=detector,
        skill_executor=executor,
        audit_writer=audit_writer,
    )
    if input_tainted:
        ctx.mark_tainted("external_input")
    return ctx, approval_store, audit_writer
