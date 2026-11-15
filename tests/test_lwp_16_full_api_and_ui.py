"""Tests für LWP-16: Vollständige P1–P12 REST-API & BITV-2.0 Web-Oberfläche."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from plattform.main import create_app


def test_bitv_dashboard_ui_and_skills_wiki_endpoints() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. BITV-2.0 Web-Dashboard (GET / und GET /workbench)
    ui_resp = client.get("/")
    assert ui_resp.status_code == 200
    html = ui_resp.text
    assert 'lang="de"' in html
    assert 'class="skip-link"' in html
    assert 'role="main"' in html
    assert "dokument.schwaerzen" in html
    assert "§ 4 Sondernutzungssatzung" in html

    wb_resp = client.get("/workbench")
    assert wb_resp.status_code == 200

    # 2. Skills-Katalog
    skills_resp = client.get("/api/v1/skills")
    assert skills_resp.status_code == 200
    skills_data = skills_resp.json()["skills"]
    assert len(skills_data) == 5

    # 3. LLM-Wiki Knoten & Normsuche
    nodes_resp = client.get("/api/v1/wiki/nodes")
    assert nodes_resp.status_code == 200
    nodes = nodes_resp.json()["nodes"]
    assert len(nodes) >= 8

    search_resp = client.get(
        "/api/v1/wiki/search",
        params={"q": "Sondernutzungsgebühr § 4", "stichtag": "2026-06-01", "top_k": 4},
    )
    assert search_resp.status_code == 200
    results = search_resp.json()["results"]
    assert len(results) >= 2
    norm_ids = [r["norm_id"] for r in results]
    assert "§ 4 Sondernutzungssatzung" in norm_ids
    assert "§ 31 VwVfG" in norm_ids


def test_edge_sse_audit_quota_pdf_and_synthetic_twin_endpoints() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Edge Lookahead SSE Stream mit PII-Schranke
    sse_resp = client.post(
        "/api/v1/edge/stream",
        json={
            "chunks": [
                "Sehr geehrte Damen und Herren, ",
                "die IBAN DE89370400440532013000 ",
                "wurde geprüft.",
            ],
            "extra_persons": ["Max Mustermann"],
        },
    )
    assert sse_resp.status_code == 200
    assert "DE89370400440532013000" not in sse_resp.text
    assert "PII_LEAK_BLOCKED" in sse_resp.text

    # 2. Dispatch + Audit-Chain Verify + Crypto-Shredding
    disp_resp = client.post(
        "/api/v1/skills/dispatch",
        json={
            "tenant_id": "stadt-musterhausen",
            "schutzklasse": 1,
            "text": "Rechtsauskunft zu § 4 Sondernutzungssatzung",
            "payload": {"fragestellung": "Wie hoch ist die Gebühr nach § 4?"},
        },
    )
    assert disp_resp.status_code == 200

    audit_resp = client.get("/api/v1/audit/stadt-musterhausen/verify")
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["chain_valid"] is True
    assert audit_data["events_count"] >= 2

    shred_resp = client.post(
        "/api/v1/audit/stadt-musterhausen/crypto-shred",
        json={"subject_ref": "buerger-4711"},
    )
    assert shred_resp.status_code == 200
    assert shred_resp.json()["chain_valid_after_shred"] is True

    # 3. Quota Endpoint
    quota_resp = client.get("/api/v1/quota/stadt-musterhausen")
    assert quota_resp.status_code == 200
    assert "budget" in quota_resp.json()

    # 4. PDF/UA Rendering & Physische PDF-Schwärzung
    pdf_resp = client.post(
        "/api/v1/documents/render-pdf-ua",
        json={
            "behoerde_name": "Stadt Musterhausen",
            "aktenzeichen": "50-WG-2026/99",
            "datum": "15.11.2026",
            "betreff": "Bescheid Wohngeld",
            "sachverhalt_zusammenfassung": "Antrag von Max Mustermann wurde vollständig geprüft.",
            "wuerdigung_satz": "Die Voraussetzungen liegen vor.",
            "zitat_wortlaut": "§ 4 Abs. 1 Satz 1 Sondernutzungssatzung",
        },
    )
    assert pdf_resp.status_code == 200
    pdf_b64 = pdf_resp.json()["pdf_base64"]
    assert len(base64.b64decode(pdf_b64)) > 500

    redact_resp = client.post(
        "/api/v1/documents/redact-pdf",
        json={
            "pdf_base64": pdf_b64,
            "sensitive_terms": ["Max Mustermann"],
        },
    )
    assert redact_resp.status_code == 200
    redact_data = redact_resp.json()
    assert redact_data["text_layer_clean"] is True
    assert redact_data["removed_spans_count"] >= 1

    # 5. Synthetic Twin Generator (P12 Alignment)
    twin_resp = client.post(
        "/api/v1/alignment/synthetic-twin",
        json={
            "original_text": "Antrag von Herrn Max Mustermann mit IBAN DE89370400440532013000.",
            "expected_skill_id": "antrag.vorpruefen",
            "expected_label": "VOLLSTAENDIG",
            "known_real_entities": ["Max Mustermann"],
        },
    )
    assert twin_resp.status_code == 200
    twin_data = twin_resp.json()
    assert twin_data["entity_overlap_ratio"] == 0.0
    assert "Max Mustermann" not in twin_data["synthetic_text"]
    assert "DE89370400440532013000" not in twin_data["synthetic_text"]
