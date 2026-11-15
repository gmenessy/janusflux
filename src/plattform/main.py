"""FastAPI-Hauptanwendung (P1–P12 Komplett-API & BITV-2.0-Web-Konsole, LWP-10 / LWP-16).
Stellt bereit:
- `GET /` & `GET /workbench` (Server-gerenderte BITV-2.0-Web-Oberfläche mit Skip-Links, Landmarks & Live-Interaktion)
- `GET /healthz`
- `GET /api/v1/skills`
- `POST /api/v1/skills/detect`
- `POST /api/v1/skills/dispatch`
- `GET /api/v1/wiki/nodes`
- `GET /api/v1/wiki/search`
- `POST /api/v1/edge/stream` (Lookahead-SSE mit PII-Schranke)
- `GET /api/v1/audit/{tenant_id}/verify`
- `POST /api/v1/audit/{tenant_id}/crypto-shred`
- `GET /api/v1/quota/{tenant_id}`
- `POST /api/v1/documents/render-pdf-ua`
- `POST /api/v1/documents/redact-pdf`
- `POST /api/v1/alignment/synthetic-twin`
- `POST /api/v1/workbench/approvals`
- `GET /api/v1/workbench/approvals/{approval_id}/view`
- `POST /api/v1/workbench/approvals/{approval_id}/decide`
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from jinja2 import Environment, select_autoescape
from pydantic import BaseModel, Field

from plattform.alignment.synthetic_twin import SyntheticTwinGenerator
from plattform.decision.dmn import DMNEngine
from plattform.document_output.pdf_ua_renderer import PdfUaRenderer
from plattform.document_output.physical_redactor import PhysicalPdfRedactor
from plattform.edge.lookahead_sse import LookaheadSSEStreamGuard
from plattform.evidence.audit import AuditWriter
from plattform.knowledge.llm_wiki import LLMWikiEngine
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.model_gateway.quota import TwoPhaseTokenQuotaManager
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

BITV_DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>JanusFlux v2.1.0+mp – BITV-2.0 Verwaltungs-Workbench &amp; LLM-Wiki</title>
  <style>
    :root {
      --bg: #f8fafc;
      --surface: #ffffff;
      --text: #0f172a;
      --muted: #334155;
      --primary: #0b4f6c;
      --primary-hover: #083b52;
      --border: #94a3b8;
      --focus: #d97706;
      --ok: #15803d;
      --warn: #b45309;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    .skip-link {
      position: absolute;
      left: -9999px;
      top: 0;
      background: var(--focus);
      color: #fff;
      padding: 0.5rem 1rem;
      z-index: 1000;
      font-weight: 700;
    }
    .skip-link:focus { left: 0.5rem; top: 0.5rem; }
    header[role="banner"] {
      background: var(--primary);
      color: #ffffff;
      padding: 1.25rem 2rem;
      border-bottom: 4px solid #083b52;
    }
    header h1 { margin: 0; font-size: 1.4rem; }
    header p { margin: 0.25rem 0 0; font-size: 0.95rem; opacity: 0.92; }
    main { max-width: 1240px; margin: 1.5rem auto; padding: 0 1.5rem; }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .card {
      background: var(--surface);
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      margin-bottom: 1.25rem;
    }
    .kpi-val { font-size: 1.75rem; font-weight: 700; color: var(--primary); }
    h2 { margin-top: 0; font-size: 1.2rem; color: var(--primary); }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td {
      text-align: left;
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.92rem;
    }
    th { background: #e2e8f0; font-weight: 700; }
    code {
      background: #f1f5f9;
      padding: 0.15rem 0.35rem;
      border-radius: 4px;
      font-size: 0.88rem;
    }
    label { display: block; font-weight: 600; margin-bottom: 0.35rem; }
    textarea, input, select {
      width: 100%;
      padding: 0.6rem;
      border: 2px solid var(--border);
      border-radius: 6px;
      font-size: 0.95rem;
      margin-bottom: 0.75rem;
    }
    button {
      background: var(--primary);
      color: #ffffff;
      border: none;
      padding: 0.65rem 1.25rem;
      border-radius: 6px;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: var(--primary-hover); }
    button:focus-visible, textarea:focus-visible, input:focus-visible {
      outline: 3px solid var(--focus);
      outline-offset: 2px;
    }
    pre {
      background: #0f172a;
      color: #f8fafc;
      padding: 1rem;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 0.85rem;
    }
  </style>
</head>
<body>
  <a href="#main-content" class="skip-link">Direkt zum Hauptinhalt springen</a>
  <header role="banner">
    <h1>JanusFlux v2.1.0+mp – Ultra-Lightweight Verwaltungsplattform</h1>
    <p>Google MediaPipe (LiteRT) • Vierstufige Skill-Engine • Strukturiertes SQLite-FTS5 LLM-Wiki • BITV 2.0 AA</p>
  </header>

  <main id="main-content" role="main">
    <section class="kpi-grid" aria-label="Plattform-Systemstatus">
      <div class="card">
        <div>Registrierte Referenz-Skills</div>
        <div class="kpi-val">{{ skills|length }}</div>
        <small>Stufen 0–3 mit Ketten-Linter</small>
      </div>
      <div class="card">
        <div>LLM-Wiki Knoten (SQLite FTS5)</div>
        <div class="kpi-val">{{ wiki_nodes|length }}</div>
        <small>Normen (SK 0/1) &amp; Skill-Knoten</small>
      </div>
      <div class="card">
        <div>DMN-Entscheidungstabellen</div>
        <div class="kpi-val">{{ dmn_tables|length }}</div>
        <small>Deterministisch (§ 35a VwVfG)</small>
      </div>
      <div class="card">
        <div>Offene HITL-Freigaben (Stufe 3)</div>
        <div class="kpi-val">{{ approvals|length }}</div>
        <small>SHA-256 Payload-Hash-Bindung</small>
      </div>
    </section>

    <section class="card" aria-labelledby="interactive-dispatch-heading">
      <h2 id="interactive-dispatch-heading">Interaktiver Skill-Detektor &amp; Ausführung (Stufen 0 → 0.5 → 1 → 0)</h2>
      <form id="dispatch-form" onsubmit="return runSkillDispatch(event)">
        <label for="req-text">Anliegen / Auftrag (Freitext oder Trigger):</label>
        <textarea id="req-text" rows="2">Bitte erstelle eine Satzungsauskunft zur Sondernutzungsgebühr nach § 4 Sondernutzungssatzung.</textarea>
        <label for="req-payload">Strukturierter Payload (JSON):</label>
        <textarea id="req-payload" rows="3">{"fragestellung": "Wie werden Gebühren nach § 4 Sondernutzungssatzung erhoben?"}</textarea>
        <button type="submit">Skill erkennen &amp; ausführen</button>
      </form>
      <pre id="dispatch-output" aria-live="polite">Bereit für Eingabe...</pre>
    </section>

    <section class="card" aria-labelledby="skills-heading">
      <h2 id="skills-heading">Deklarativer Skill-Katalog (skills/*/skill.yaml)</h2>
      <table>
        <thead>
          <tr>
            <th>Skill-ID</th>
            <th>Name</th>
            <th>Stufen</th>
            <th>Wirkung</th>
            <th>LLM-Budget</th>
          </tr>
        </thead>
        <tbody>
          {% for s in skills %}
          <tr>
            <td><code>{{ s.skill_id }}</code></td>
            <td>{{ s.name }}</td>
            <td><code>{{ s.stufen }}</code></td>
            <td><code>{{ s.wirkung }}</code></td>
            <td>{{ s.budget.llm_aufrufe }} Aufruf(e) / max. {{ s.budget.max_input_tokens }} Tok.</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>

    <section class="card" aria-labelledby="wiki-heading">
      <h2 id="wiki-heading">Strukturiertes LLM-Wiki &amp; Nativer 1-Hop-Normgraph (SQLite FTS5 / BM25)</h2>
      <table>
        <thead>
          <tr>
            <th>Knoten-ID</th>
            <th>Typ</th>
            <th>Norm / Skill</th>
            <th>Titel</th>
            <th>Schutzklasse</th>
            <th>Explizite [[Wiki-Links]]</th>
          </tr>
        </thead>
        <tbody>
          {% for n in wiki_nodes %}
          <tr>
            <td><code>{{ n.node_id }}</code></td>
            <td>{{ n.node_type }}</td>
            <td><code>{{ n.norm_id or n.skill_id }}</code></td>
            <td>{{ n.title }}</td>
            <td>SK {{ n.schutzklasse }}</td>
            <td><code>{{ n.links|join(", ") if n.links else "–" }}</code></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>
  </main>

  <script>
    async function runSkillDispatch(ev) {
      ev.preventDefault();
      const out = document.getElementById('dispatch-output');
      out.textContent = 'Verarbeite über SkillDetector & SkillExecutor...';
      try {
        const text = document.getElementById('req-text').value;
        const payload = JSON.parse(document.getElementById('req-payload').value || '{}');
        const res = await fetch('/api/v1/skills/dispatch', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            tenant_id: 'stadt-musterhausen',
            schutzklasse: 1,
            text: text,
            payload: payload
          })
        });
        const data = await res.json();
        out.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        out.textContent = 'Fehler: ' + err;
      }
      return false;
    }
  </script>
</body>
</html>"""


class SkillRequestModel(BaseModel):
    tenant_id: str = "stadt-musterhausen"
    schutzklasse: int = Field(default=2, ge=0, le=3)
    text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    explicit_skill_id: str | None = None
    input_tainted: bool = True
    allowed_skills: list[str] | None = None


class SSEStreamRequest(BaseModel):
    chunks: list[str]
    extra_persons: list[str] = Field(default_factory=list)


class CryptoShredRequest(BaseModel):
    subject_ref: str


class RenderPdfRequest(BaseModel):
    behoerde_name: str = "Stadt Musterhausen"
    aktenzeichen: str = "50-WG-2026/104"
    datum: str = "15.11.2026"
    betreff: str = "Verwaltungsbescheid"
    sachverhalt_zusammenfassung: str
    wuerdigung_satz: str
    zitat_wortlaut: str
    title: str = "Verwaltungsbescheid"


class RedactPdfRequest(BaseModel):
    pdf_base64: str | None = None
    dokument_text: str = "Stellungnahme von Herrn Max Mustermann (DE89370400440532013000)."
    sensitive_terms: list[str] = Field(
        default_factory=lambda: ["Max Mustermann", "DE89370400440532013000"]
    )


class SyntheticTwinRequest(BaseModel):
    original_text: str
    expected_skill_id: str
    expected_label: str
    known_real_entities: list[str] = Field(default_factory=list)


def create_app() -> FastAPI:
    application = FastAPI(
        title="JanusFlux – MediaPipe Ultra-Lightweight Skill & LLM-Wiki Platform",
        version="2.1.0+mp",
    )

    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(Path("skills"), wiki_dir=Path("wiki"))
    registry.load_all(embedder_fn=gateway.embed_sync)
    wiki_engine = LLMWikiEngine(Path("wiki"))
    dmn_engine = DMNEngine(Path("rules"))
    approval_store = WorkbenchApprovalStore()
    audit_writer = AuditWriter()
    quota_manager = TwoPhaseTokenQuotaManager()
    pdf_renderer = PdfUaRenderer()
    pdf_redactor = PhysicalPdfRedactor()
    twin_generator = SyntheticTwinGenerator()
    jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

    handlers = build_builtin_action_handlers(
        dmn_engine=dmn_engine,
        gateway=gateway,
        approval_store=approval_store,
    )
    detector = SkillDetector(registry=registry, media_pipe_gateway=gateway, dmn_engine=dmn_engine)
    executor = SkillExecutor(registry=registry, action_handlers=handlers)

    application.state.gateway = gateway
    application.state.registry = registry
    application.state.wiki_engine = wiki_engine
    application.state.dmn_engine = dmn_engine
    application.state.approval_store = approval_store
    application.state.audit_writer = audit_writer
    application.state.quota_manager = quota_manager
    application.state.detector = detector
    application.state.executor = executor

    application.include_router(create_workbench_router(approval_store))

    @application.get("/", response_class=HTMLResponse)
    @application.get("/workbench", response_class=HTMLResponse)
    async def index_workbench_ui() -> HTMLResponse:
        tmpl = jinja_env.from_string(BITV_DASHBOARD_HTML_TEMPLATE)
        html = tmpl.render(
            skills=registry.list_all(),
            wiki_nodes=list(wiki_engine._nodes.values()),
            dmn_tables=list(dmn_engine._tables.keys()),
            approvals=list(approval_store._approvals.values()),
        )
        return HTMLResponse(content=html, status_code=200)

    @application.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "2.1.0+mp",
            "skills_loaded": len(registry.list_all()),
            "wiki_nodes_loaded": len(wiki_engine._nodes),
            "dmn_tables_loaded": len(dmn_engine._tables),
        }

    @application.get("/api/v1/skills")
    async def list_skills_endpoint() -> dict[str, Any]:
        return {
            "skills": [s.model_dump() for s in registry.list_all()],
        }

    @application.post("/api/v1/skills/detect")
    async def detect_skill_endpoint(req: SkillRequestModel) -> dict[str, Any]:
        allowed = (
            set(req.allowed_skills) if req.allowed_skills is not None else DEFAULT_ALLOWED_SKILLS
        )
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
        allowed = (
            set(req.allowed_skills) if req.allowed_skills is not None else DEFAULT_ALLOWED_SKILLS
        )
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
            out_dict = result["execution"].get("output", {})
            if isinstance(out_dict, dict) and "redacted_pdf_bytes" in out_dict:
                out_dict["redacted_pdf_bytes"] = f"<{len(out_dict['redacted_pdf_bytes'])} bytes>"
        return result

    @application.get("/api/v1/wiki/nodes")
    async def list_wiki_nodes_endpoint() -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "title": n.title,
                    "norm_id": n.norm_id,
                    "skill_id": n.skill_id,
                    "schutzklasse": n.schutzklasse,
                    "gueltig_ab": n.gueltig_ab.isoformat(),
                    "gueltig_bis": n.gueltig_bis.isoformat() if n.gueltig_bis else None,
                    "links": n.links,
                }
                for n in wiki_engine._nodes.values()
            ]
        }

    @application.get("/api/v1/wiki/search")
    async def search_wiki_norms_endpoint(
        q: str = Query(..., description="Suchanfrage für das Rechts- und Satzungs-Wiki"),
        stichtag: str | None = Query(None, description="Bi-temporaler Stichtag YYYY-MM-DD"),
        top_k: int = Query(4, ge=1, le=4),
    ) -> dict[str, Any]:
        parsed_date = date.fromisoformat(stichtag) if stichtag else None
        chunks = wiki_engine.search_norms(query=q, stichtag=parsed_date, top_k=top_k, expand_1hop=True)
        return {
            "query": q,
            "results": [
                {
                    "chunk_id": c.chunk_id,
                    "norm_id": c.norm_id,
                    "titel": c.titel,
                    "context_header": c.context_header,
                    "raw_text": c.raw_text,
                    "referenced_norms": c.referenced_norms,
                }
                for c in chunks
            ],
        }

    @application.post("/api/v1/edge/stream")
    async def lookahead_sse_endpoint(req: SSEStreamRequest) -> StreamingResponse:
        guard = LookaheadSSEStreamGuard(extra_persons=req.extra_persons)

        async def _chunk_gen() -> AsyncIterator[str]:
            for ch in req.chunks:
                yield ch

        return StreamingResponse(
            guard.filter_token_stream(_chunk_gen()),
            media_type="text/event-stream",
        )

    @application.get("/api/v1/audit/{tenant_id}/verify")
    async def verify_audit_endpoint(tenant_id: str) -> dict[str, Any]:
        valid = await audit_writer.verify_chain(tenant_id)
        events = await audit_writer.list_events(tenant_id)
        return {
            "tenant_id": tenant_id,
            "chain_valid": valid,
            "events_count": len(events),
            "last_hash": events[-1].entry_hash if events else "GENESIS",
        }

    @application.post("/api/v1/audit/{tenant_id}/crypto-shred")
    async def crypto_shred_endpoint(tenant_id: str, req: CryptoShredRequest) -> dict[str, Any]:
        deleted = await audit_writer.crypto_shred_subject(tenant_id, req.subject_ref)
        valid_after = await audit_writer.verify_chain(tenant_id)
        return {
            "tenant_id": tenant_id,
            "deleted_salts": deleted,
            "chain_valid_after_shred": valid_after,
        }

    @application.get("/api/v1/quota/{tenant_id}")
    async def get_quota_endpoint(tenant_id: str) -> dict[str, Any]:
        return await quota_manager.get_usage(tenant_id)

    @application.post("/api/v1/documents/render-pdf-ua")
    async def render_pdf_ua_endpoint(req: RenderPdfRequest) -> dict[str, Any]:
        artifact = pdf_renderer.render_bescheid(
            context=req.model_dump(),
            title=req.title,
        )
        return {
            "title": artifact.title,
            "language": artifact.language,
            "pdf_ua_part": artifact.pdf_ua_part,
            "size_bytes": len(artifact.pdf_bytes),
            "pdf_base64": base64.b64encode(artifact.pdf_bytes).decode("ascii"),
        }

    @application.post("/api/v1/documents/redact-pdf")
    async def redact_pdf_endpoint(req: RedactPdfRequest) -> dict[str, Any]:
        if req.pdf_base64:
            raw_pdf = base64.b64decode(req.pdf_base64)
        else:
            rendered = pdf_renderer.render_bescheid(
                {
                    "behoerde_name": "Stadt Musterhausen",
                    "aktenzeichen": "IFG-2026/01",
                    "datum": "15.11.2026",
                    "betreff": "IFG-Schwärzung",
                    "sachverhalt_zusammenfassung": req.dokument_text,
                    "wuerdigung_satz": "Physische Glyphen-Entfernung.",
                    "zitat_wortlaut": "§ 4 IFG",
                }
            )
            raw_pdf = rendered.pdf_bytes

        res = pdf_redactor.redact_sensitive_terms(raw_pdf, sensitive_terms=req.sensitive_terms)
        return {
            "removed_spans_count": res.removed_spans_count,
            "text_layer_clean": res.text_layer_clean,
            "redacted_pdf_base64": base64.b64encode(res.redacted_pdf_bytes).decode("ascii"),
        }

    @application.post("/api/v1/alignment/synthetic-twin")
    async def synthetic_twin_endpoint(req: SyntheticTwinRequest) -> dict[str, Any]:
        twin = twin_generator.generate_twin(
            original_text=req.original_text,
            expected_skill_id=req.expected_skill_id,
            expected_label=req.expected_label,
            known_real_entities=req.known_real_entities,
        )
        return {
            "synthetic_text": twin.synthetic_text,
            "expected_skill_id": twin.expected_skill_id,
            "expected_label": twin.expected_label,
            "replaced_entities_count": twin.replaced_entities_count,
            "entity_overlap_ratio": twin.entity_overlap_ratio,
        }

    return application


app = create_app()
