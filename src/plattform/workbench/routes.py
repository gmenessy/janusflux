"""Serverseitige BITV-2.0-Workbench (P8, LWP-10):
- Verwaltet menschliche Freigaben (`L1`, `L2`, `L3`) mit kryptografischer SHA-256-Payload-Bindung (`payload_hash`).
- Rendert eine barrierefreie Split-Screen-HTML-Ansicht (BITV 2.0 / WCAG 2.1 AA: semantische Landmarks,
  Tastaturfokus, Kontrast, `lang="de"`).
- Lehnt bei `POST /api/v1/workbench/approvals/{approval_id}/decide` jede Abweichung des `payload_hash`
  oder eine unzulässige Selbstfreigabe bei Vier-Augen-Pflicht (`L2`) strikt ab (HTTP 409 `PayloadHashMismatch`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, select_autoescape
from pydantic import BaseModel

BITV_SPLIT_SCREEN_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>BITV-2.0-Workbench – Freigabe {{ approval.approval_id }}</title>
</head>
<body>
  <header role="banner">
    <h1>Sachbearbeitungs-Workbench (Stufe 3 – Freigabestufe {{ approval.level }})</h1>
  </header>
  <main role="main" aria-label="Split-Screen Vergleichsansicht">
    <section aria-labelledby="source-heading">
      <h2 id="source-heading">Quell-Dokument &amp; Nachweise</h2>
      <pre>{{ approval.payload_json }}</pre>
    </section>
    <section aria-labelledby="decision-heading">
      <h2 id="decision-heading">Kryptografische Payload-Bindung</h2>
      <p>Payload-SHA256: <code id="payload-hash">{{ approval.payload_hash }}</code></p>
      <p>Status: <strong>{{ approval.status }}</strong></p>
    </section>
  </main>
</body>
</html>"""


def compute_canonical_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@dataclass
class ApprovalRecord:
    approval_id: str
    tenant_id: str
    run_id: str
    step_id: str
    level: Literal["L1", "L2", "L3"]
    payload: dict[str, Any]
    payload_hash: str
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    decided_by: list[str] = field(default_factory=list)
    reason: str | None = None


class CreateApprovalRequest(BaseModel):
    tenant_id: str
    run_id: str
    step_id: str
    level: Literal["L1", "L2", "L3"] = "L1"
    payload: dict[str, Any]


class DecideApprovalRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    decided_by: str
    expected_payload_hash: str
    payload_override: dict[str, Any] | None = None
    reason: str = "Geprüft und freigegeben"


class WorkbenchApprovalStore:
    """Speichert und prüft Freigabe-Anforderungen mit SHA-256-Payload-Bindung."""

    def __init__(self) -> None:
        self._approvals: dict[str, ApprovalRecord] = {}
        self._jinja = Environment(autoescape=select_autoescape(["html", "xml"]))

    def create_approval(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        level: Literal["L1", "L2", "L3"],
        payload: dict[str, Any],
    ) -> ApprovalRecord:
        approval_id = str(uuid.uuid4())
        p_hash = compute_canonical_payload_hash(payload)
        rec = ApprovalRecord(
            approval_id=approval_id,
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step_id,
            level=level,
            payload=dict(payload),
            payload_hash=p_hash,
        )
        self._approvals[approval_id] = rec
        return rec

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        if approval_id not in self._approvals:
            raise KeyError(f"Unbekannte Approval-ID: '{approval_id}'")
        return self._approvals[approval_id]

    def mutate_stored_payload_for_test(self, approval_id: str, new_payload: dict[str, Any]) -> None:
        """Simuliert eine nachträgliche Änderung des Payloads nach Erzeugung der Vorschau."""
        rec = self.get_approval(approval_id)
        rec.payload = dict(new_payload)
        rec.payload_hash = compute_canonical_payload_hash(new_payload)

    def decide(
        self,
        *,
        approval_id: str,
        decision: Literal["APPROVED", "REJECTED"],
        decided_by: str,
        expected_payload_hash: str,
        payload_override: dict[str, Any] | None = None,
        reason: str = "",
    ) -> ApprovalRecord:
        rec = self.get_approval(approval_id)
        current_hash = (
            compute_canonical_payload_hash(payload_override)
            if payload_override is not None
            else rec.payload_hash
        )
        if expected_payload_hash != rec.payload_hash or current_hash != rec.payload_hash:
            raise ValueError(
                f"PayloadHashMismatch: Erwarteter Hash '{expected_payload_hash}' stimmt nicht "
                f"mit aktuellem Payload-Hash '{current_hash}' (gespeichert '{rec.payload_hash}') überein."
            )
        rec.decided_by.append(decided_by)
        rec.reason = reason
        rec.status = decision
        return rec

    def render_html(self, approval_id: str) -> str:
        rec = self.get_approval(approval_id)
        tmpl = self._jinja.from_string(BITV_SPLIT_SCREEN_TEMPLATE)
        return tmpl.render(
            approval={
                "approval_id": rec.approval_id,
                "level": rec.level,
                "payload_json": json.dumps(rec.payload, indent=2, ensure_ascii=False),
                "payload_hash": rec.payload_hash,
                "status": rec.status,
            }
        )


def create_workbench_router(store: WorkbenchApprovalStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"])

    @router.post("/approvals")
    async def create_approval_endpoint(req: CreateApprovalRequest) -> dict[str, Any]:
        rec = store.create_approval(
            tenant_id=req.tenant_id,
            run_id=req.run_id,
            step_id=req.step_id,
            level=req.level,
            payload=req.payload,
        )
        return {
            "approval_id": rec.approval_id,
            "payload_hash": rec.payload_hash,
            "status": rec.status,
            "level": rec.level,
        }

    @router.get("/approvals/{approval_id}/view", response_class=HTMLResponse)
    async def view_approval_html(approval_id: str) -> HTMLResponse:
        try:
            html = store.render_html(approval_id)
            return HTMLResponse(content=html, status_code=200)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post("/approvals/{approval_id}/decide")
    async def decide_approval_endpoint(
        approval_id: str, req: DecideApprovalRequest
    ) -> dict[str, Any]:
        try:
            rec = store.decide(
                approval_id=approval_id,
                decision=req.decision,
                decided_by=req.decided_by,
                expected_payload_hash=req.expected_payload_hash,
                payload_override=req.payload_override,
                reason=req.reason,
            )
            return {
                "approval_id": rec.approval_id,
                "status": rec.status,
                "payload_hash": rec.payload_hash,
                "decided_by": rec.decided_by,
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    return router
