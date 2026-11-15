"""FastAPI-Hauptanwendung (P1/P6/P8, LWP-10):
Stellt bereit:
- `GET /healthz`
- `POST /api/v1/skills/detect`
- `POST /api/v1/skills/dispatch`
- `POST /api/v1/workbench/approvals`
- `GET /api/v1/workbench/approvals/{approval_id}/view`
- `POST /api/v1/workbench/approvals/{approval_id}/decide`
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from plattform.decision.dmn import DMNEngine
from plattform.evidence.audit import AuditWriter
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.runtime.context import RunContext
from plattform.skills.builtin_handlers import build_builtin_action_handlers
from plattform.skills.detector import SkillDetector
from plattform.skills.executor import SkillExecutor
from plattform.skills.registry import SkillRegistry
from plattform.workbench.routes import WorkbenchApprovalStore, create_workbench_router

DEFAULT_ALLOWED_SKILLS: set[str] = {
    "dokument.schwaerzen",
    "antrag.vorpruefen",
    "dokument.extrahieren",
    "auskunft.recht",
    "eingang.routen",
}


class SkillRequestModel(BaseModel):
    tenant_id: str = "stadt-musterhausen"
    schutzklasse: int = Field(default=2, ge=0, le=3)
    text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    explicit_skill_id: str | None = None
    input_tainted: bool = True
    allowed_skills: list[str] | None = None


def create_app() -> FastAPI:
    application = FastAPI(
        title="JanusFlux – MediaPipe Ultra-Lightweight Skill Platform",
        version="2.1.0+mp",
    )

    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"))
    registry.load_all(embedder_fn=gateway.embed_sync)
    dmn_engine = DMNEngine(Path("rules"))
    approval_store = WorkbenchApprovalStore()
    audit_writer = AuditWriter()

    handlers = build_builtin_action_handlers(
        dmn_engine=dmn_engine,
        gateway=gateway,
        approval_store=approval_store,
    )
    detector = SkillDetector(registry=registry, media_pipe_gateway=gateway, dmn_engine=dmn_engine)
    executor = SkillExecutor(registry=registry, action_handlers=handlers)

    application.state.gateway = gateway
    application.state.registry = registry
    application.state.dmn_engine = dmn_engine
    application.state.approval_store = approval_store
    application.state.audit_writer = audit_writer
    application.state.detector = detector
    application.state.executor = executor

    application.include_router(create_workbench_router(approval_store))

    @application.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "skills_loaded": len(registry.list_all()),
        }

    @application.post("/api/v1/skills/detect")
    async def detect_skill_endpoint(req: SkillRequestModel) -> dict[str, Any]:
        allowed = set(req.allowed_skills) if req.allowed_skills is not None else DEFAULT_ALLOWED_SKILLS
        res = await detector.detect(
            text=req.text,
            payload=req.payload,
            allowed_skill_ids=allowed,
            schutzklasse=req.schutzklasse,
            input_tainted=req.input_tainted,
            explicit_skill_id=req.explicit_skill_id,
        )
        return res.model_dump()

    @application.post("/api/v1/skills/dispatch")
    async def dispatch_skill_endpoint(req: SkillRequestModel) -> dict[str, Any]:
        allowed = set(req.allowed_skills) if req.allowed_skills is not None else DEFAULT_ALLOWED_SKILLS
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            tenant_id=req.tenant_id,
            schutzklasse=req.schutzklasse,
            allowed_skills=allowed,
            skill_detector=detector,
            skill_executor=executor,
            audit_writer=audit_writer,
        )
        if req.input_tainted:
            ctx.mark_tainted("request_input")
        result = await ctx.detect_and_run_skill(
            text=req.text,
            payload=req.payload,
            explicit_skill_id=req.explicit_skill_id,
        )
        if "execution" in result and isinstance(result["execution"], dict):
            # Entferne rohe PDF-Bytes aus JSON-Antworten
            out_dict = result["execution"].get("output", {})
            if isinstance(out_dict, dict) and "redacted_pdf_bytes" in out_dict:
                out_dict["redacted_pdf_bytes"] = f"<{len(out_dict['redacted_pdf_bytes'])} bytes>"
        return result

    return application


app = create_app()
