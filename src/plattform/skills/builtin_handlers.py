"""Builtin Action-Handler für die 5 Referenz-Skills (LWP-10 / LWP-11).
Verbindet die deklarierten Skill-Schritte (`p2.*`, `p3.*`, `p4.*`, `p5.*`, `p8.*`, `p11.*`)
mit den deterministischen und MediaPipe-gestützten Plattform-Diensten.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from plattform.decision.dmn import DMNEngine
from plattform.decision.fristen import berechne_frist_vwvfg
from plattform.decision.post_gate import PostExecutionGate
from plattform.document_output.pdf_ua_renderer import PdfUaRenderer
from plattform.document_output.physical_redactor import PhysicalPdfRedactor
from plattform.knowledge.chunker import LightweightLegalChunker
from plattform.knowledge.llm_wiki import LLMWikiEngine
from plattform.knowledge.retriever import LightweightHybridRetriever
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.policy_privacy.pii_detectors import (
    detect_all_pii_spans,
    extract_deterministic_fields,
)
from plattform.policy_privacy.redaction_vault import RedactionVault
from plattform.workbench.routes import WorkbenchApprovalStore

DEFAULT_CORPUS_TEXT = """
§ 31 Fristen und Termine
(1) Für die Berechnung von Fristen und für die Bestimmung von Terminen gelten die §§ 187 bis 193 des Bürgerlichen Gesetzbuches entsprechend, soweit nicht durch die §§ 32 bis 35 etwas anderes bestimmt ist.
(3) Fällt das Ende einer Frist auf einen Sonntag, einen gesetzlichen Feiertag oder einen Sonnabend, so endet die Frist mit dem Ablauf des nächstfolgenden Werktages.

§ 35a Vollständig automatisierter Erlass eines Verwaltungsaktes
Ein Verwaltungsakt kann vollständig durch automatische Einrichtungen erlassen werden, sofern dies durch Rechtsvorschrift zugelassen ist und weder ein Ermessen noch ein Beurteilungsspielraum besteht.
"""


def build_builtin_action_handlers(
    *,
    dmn_engine: DMNEngine | None = None,
    gateway: MediaPipeGatewayAdapter | None = None,
    retriever: LightweightHybridRetriever | None = None,
    approval_store: WorkbenchApprovalStore | None = None,
    post_gate: PostExecutionGate | None = None,
) -> dict[str, Any]:
    dmn = dmn_engine or DMNEngine()
    mp = gateway or MediaPipeGatewayAdapter()
    ret = retriever or LightweightHybridRetriever(gateway=mp)
    wb = approval_store or WorkbenchApprovalStore()
    gate = post_gate or PostExecutionGate()
    pdf_renderer = PdfUaRenderer()
    redactor = PhysicalPdfRedactor()

    async def _ensure_retriever_seeded() -> None:
        if not ret._chunks:
            chunker = LightweightLegalChunker()
            chunks = chunker.chunk_statute(
                doc_id="vwvfg_seed", gesetz_kurz="VwVfG", text=DEFAULT_CORPUS_TEXT
            )
            await ret.index_chunks(chunks)

    # --- P2: Privacy & Deterministische Vorverarbeitung ---
    async def h_p2_redact_input(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        vault = RedactionVault()
        raw = str(state.get("eingang_text", state.get("dokument_text", "")))
        return {"redacted_view": vault.redact_text(raw)}

    async def h_p2_detect_pii_bboxes(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        doc_text = str(
            state.get(
                "dokument_text",
                "Stellungnahme von Herrn Max Mustermann (IBAN DE89370400440532013000).",
            )
        )
        known = state.get("sensitive_terms", ["Max Mustermann"])
        spans = detect_all_pii_spans(doc_text, extra_persons=known)
        terms = list({s.text for s in spans} | set(known))
        return {"detected_pii_terms": terms, "dokument_text": doc_text}

    async def h_p2_extract_deterministic_fields(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        doc_text = str(state.get("dokument_text", ""))
        det = extract_deterministic_fields(doc_text)
        felder: dict[str, Any] = dict(state.get("felder", {}))
        for k, val in det.items():
            if val is not None:
                felder[k] = {"wert": val, "zitat": val, "konfidenz_pfad": "auto"}
        return {"felder": felder, "source_redacted_views": [doc_text]}

    # --- P3: Decision Port & DMN ---
    async def h_p3_check_mandatory_fields(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        form = state.get("antrag_formular", {})
        required_form_keys = state.get("pflichtfelder", ["antragsteller", "anschrift"])
        complete = isinstance(form, dict) and all(
            bool(form.get(k)) for k in required_form_keys
        )
        return {"pflichtfelder_vollstaendig": complete}

    async def h_p3_decide(
        ctx: Any, state: dict[str, Any], step_spec: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        contract = step_spec.get("contract", "")
        if contract == "antrag.nachweise":
            anlagen = " ".join(str(x) for x in state.get("anlagen_texte", [])).lower()
            has_einkommen = any(
                w in anlagen for w in ("einkommensnachweis", "gehaltsabrechnung", "lohnabrechnung")
            )
            has_miete = any(w in anlagen for w in ("mietvertrag", "mietbescheinigung", "kaltmiete"))
            if "unklar" in anlagen or "unleserlich" in anlagen:
                return {
                    "einkommensnachweis_pfad": "human",
                    "mietnachweis_pfad": "human",
                }
            return {
                "einkommensnachweis_pfad": "yes" if has_einkommen else "no",
                "mietnachweis_pfad": "yes" if has_miete else "no",
            }
        if contract == "eingang.gate":
            txt = str(state.get("redacted_view") or state.get("eingang_text", "")).lower()
            is_urgent = any(w in txt for w in ("eilantrag", "fristablauf", "sofortvollzug"))
            if any(w in txt for w in ("wohngeld", "miete", "sozialhilfe", "bürgergeld")):
                return {
                    "anliegen_pfad": "auto",
                    "anliegen_wert": "wohngeld_soziales",
                    "frist_oder_eilfall_pfad": "yes" if is_urgent else "no",
                }
            if any(w in txt for w in ("baugenehmigung", "sondernutzung", "bauantrag")):
                return {
                    "anliegen_pfad": "auto",
                    "anliegen_wert": "bau_sondernutzung",
                    "frist_oder_eilfall_pfad": "yes" if is_urgent else "no",
                }
            return {
                "anliegen_pfad": "human",
                "anliegen_wert": "allgemeine_anfrage",
                "frist_oder_eilfall_pfad": "yes" if is_urgent else "no",
            }
        if contract == "schwaerzung.pruefung":
            return {"schwaerzung_erforderlich_pfad": "yes"}
        return {}

    async def h_p3_rule(
        ctx: Any, state: dict[str, Any], step_spec: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        table_id = str(step_spec.get("table_id", ""))
        dmn_res = dmn.evaluate(table_id, state)
        out = dict(dmn_res.output)
        out["dmn_rule_id"] = dmn_res.matched_rule_ids[0]
        out["dmn_begruendung"] = dmn_res.begruendung_text
        if "frist_tage" in out:
            tage = int(out["frist_tage"])
            if tage > 0:
                ref_date = state.get("ereignis_datum")
                base_date = (
                    date.fromisoformat(ref_date)
                    if isinstance(ref_date, str)
                    else date(2026, 11, 1)
                )
                frist_erg = berechne_frist_vwvfg(base_date, tage=tage)
                out["frist_datum"] = frist_erg.effektives_fristende.isoformat()
                out["expected_frist_datum"] = out["frist_datum"]
            else:
                out["frist_datum"] = None
        return out

    async def h_p3_validate_output(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        gate.enforce(
            output_data={
                "korb": state.get("korb"),
                "aktion": state.get("aktion"),
                "frist_datum": state.get("frist_datum"),
                "nachforderung_text": state.get("nachforderung_text"),
            },
            expected_frist_datum=state.get("expected_frist_datum"),
            forbid_pii=True,
        )
        return {"post_gate_passed": True}

    async def h_p3_verify_exact_quotes(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        sources = state.get("source_redacted_views") or [str(state.get("dokument_text", ""))]
        if "zitat_wortlaut" in state:
            gate.enforce(
                output_data={
                    "antwort_satz": state.get("antwort_satz", ""),
                    "zitat_wortlaut": state.get("zitat_wortlaut", ""),
                    "norm_fundstelle": state.get("norm_fundstelle", ""),
                },
                source_redacted_views=sources,
                allowed_norms=set(state.get("allowed_norms", [])) or None,
                forbid_pii=True,
            )
        else:
            gate.enforce(
                output_data=state.get("felder", {}),
                source_redacted_views=sources,
                forbid_pii=False,
            )
        return {"quotes_verified": True}

    # --- P4: Strukturiertes LLM-Wiki (SQLite FTS5 / BM25 + nativer 1-Hop-Verweisgraph) ---
    wiki_engine = LLMWikiEngine()

    async def h_p4_retrieve_top4(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        if ctx.schutzklasse > 2:
            raise PermissionError(
                "Rechtsauskunft über das offene LLM-Wiki ist auf Schutzklassen 0–2 begrenzt."
            )
        query = str(state.get("fragestellung", ""))
        stichtag_raw = state.get("stichtag")
        stichtag = date.fromisoformat(stichtag_raw) if isinstance(stichtag_raw, str) else None
        if wiki_engine._nodes:
            chunks = wiki_engine.search_norms(query, stichtag=stichtag, top_k=4, expand_1hop=True)
        else:
            await _ensure_retriever_seeded()
            chunks = await ret.retrieve(query, stichtag=stichtag, top_k=4)
        return {
            "retrieved_chunks": [
                {"norm_id": c.norm_id, "raw_text": c.raw_text} for c in chunks
            ],
            "source_redacted_views": [c.raw_text for c in chunks],
            "allowed_norms": [c.norm_id for c in chunks],
        }

    # --- P5: MediaPipe Generation / Structured Extraction ---
    async def h_p5_generate_if_rot(ctx: Any, state: dict[str, Any], **_: Any) -> dict[str, Any]:
        if state.get("korb") != "ROT":
            return {"nachforderung_text": None}
        fehlende: list[str] = []
        if not state.get("pflichtfelder_vollstaendig", True):
            fehlende.append("Pflichtangaben im Antragsformular")
        if state.get("einkommensnachweis_pfad") == "no":
            fehlende.append("Einkommensnachweis")
        if state.get("mietnachweis_pfad") == "no":
            fehlende.append("Mietbescheinigung")
        frist = state.get("frist_datum", "")
        text = (
            f"Bitte reichen Sie folgende Unterlagen bis zum {frist} (§ 31 VwVfG) nach: "
            + ", ".join(fehlende)
            + "."
        )
        return {"nachforderung_text": text}

    async def h_p5_extract_remaining_fields(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        doc_text = str(state.get("dokument_text", ""))
        zielfelder: list[str] = list(state.get("zielfelder", []))
        felder: dict[str, Any] = dict(state.get("felder", {}))

        sentences = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", doc_text) if s.strip()]
        for zf in zielfelder:
            if zf in felder:
                continue
            matched_sent = next(
                (s for s in sentences if zf.lower() in s.lower()),
                sentences[0] if sentences else doc_text,
            )
            m_val = re.search(r"(\d{1,4}(?:[.,]\d{2})?\s*(?:EUR|€)|\d{2}\.\d{2}\.\d{4})", matched_sent)
            wert = m_val.group(1) if m_val else matched_sent
            felder[zf] = {
                "wert": wert,
                "zitat": matched_sent,
                "konfidenz_pfad": "auto",
            }
        return {"felder": felder, "source_redacted_views": [doc_text]}

    async def h_p5_synthesize_legal_answer(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        chunks = state.get("retrieved_chunks", [])
        if not chunks:
            return {
                "antwort_satz": "Keine einschlägige Rechtsnorm im Bestand gefunden.",
                "zitat_wortlaut": "",
                "norm_fundstelle": "",
            }
        top = chunks[0]
        raw_lines = [ln.strip() for ln in str(top["raw_text"]).splitlines() if ln.strip()]
        quote_line = raw_lines[-1] if len(raw_lines) > 1 else raw_lines[0]
        return {
            "antwort_satz": f"Gemäß {top['norm_id']} gilt für das Anliegen folgende gesetzliche Regelung.",
            "zitat_wortlaut": quote_line,
            "norm_fundstelle": str(top["norm_id"]),
        }

    # --- P8: BITV-2.0-Workbench Approval ---
    async def h_p8_approve(
        ctx: Any, state: dict[str, Any], step_spec: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        if state.get("human_approved") is True:
            return {"approval_status": "APPROVED"}
        level = step_spec.get("level", "L1")
        rec = wb.create_approval(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            step_id=step_spec.get("step_id", "s3_human_approval"),
            level=level,
            payload={
                "doc_id": state.get("doc_id"),
                "detected_pii_terms": state.get("detected_pii_terms", []),
            },
        )
        return {
            "__status__": "WAITING_HUMAN",
            "approval_id": rec.approval_id,
            "payload_hash": rec.payload_hash,
        }

    # --- P11: Physische PDF-Schwärzung ---
    async def h_p11_redact_pdf_physical(
        ctx: Any, state: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        pdf_bytes: bytes | None = state.get("pdf_bytes")
        if pdf_bytes is None:
            rendered = pdf_renderer.render_bescheid(
                {
                    "behoerde_name": "Stadt Musterhausen",
                    "aktenzeichen": "IFG-2026/01",
                    "datum": "15.11.2026",
                    "betreff": "Dokument zur Offenlegung",
                    "sachverhalt_zusammenfassung": str(
                        state.get(
                            "dokument_text",
                            "Herr Max Mustermann (DE89370400440532013000) hat Stellung genommen.",
                        )
                    ),
                    "wuerdigung_satz": "Offenlegung nach Schwärzung.",
                    "zitat_wortlaut": "§ 4 IFG",
                }
            )
            pdf_bytes = rendered.pdf_bytes

        terms = list(state.get("detected_pii_terms", ["Max Mustermann", "DE89370400440532013000"]))
        res = redactor.redact_sensitive_terms(pdf_bytes, sensitive_terms=terms)
        return {
            "redacted_doc_id": f"redacted-{state.get('doc_id', 'doc')}",
            "removed_spans_count": res.removed_spans_count,
            "text_layer_clean": res.text_layer_clean,
            "redacted_pdf_bytes": res.redacted_pdf_bytes,
        }

    return {
        "p2.redact_input": h_p2_redact_input,
        "p2.detect_pii_bboxes": h_p2_detect_pii_bboxes,
        "p2.extract_deterministic_fields": h_p2_extract_deterministic_fields,
        "p3.check_mandatory_fields": h_p3_check_mandatory_fields,
        "p3.decide": h_p3_decide,
        "p3.rule": h_p3_rule,
        "p3.validate_output": h_p3_validate_output,
        "p3.verify_exact_quotes": h_p3_verify_exact_quotes,
        "p4.retrieve_top4": h_p4_retrieve_top4,
        "p5.generate_if_rot": h_p5_generate_if_rot,
        "p5.extract_remaining_fields": h_p5_extract_remaining_fields,
        "p5.synthesize_legal_answer": h_p5_synthesize_legal_answer,
        "p8.approve": h_p8_approve,
        "p11.redact_pdf_physical": h_p11_redact_pdf_physical,
    }
