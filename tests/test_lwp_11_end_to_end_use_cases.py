"""Abnahmetests für LWP-11: Referenz-Use-Cases A bis F über die Skill-Engine verknüpfen."""

from workflows.unified_skill_router import (
    create_default_runtime_context,
    run_unified_skill_workflow,
)


async def test_lwp_11_use_case_a_posteingangs_routing() -> None:
    ctx, _, audit = create_default_runtime_context()
    res = await run_unified_skill_workflow(
        ctx,
        text="Posteingang klassifizieren und dem zuständigen Fachamt zuordnen.",
        payload={"eingang_text": "Ich beantrage Wohngeld wegen gestiegener Kaltmiete."},
    )
    assert res["detection"]["detected_skill_id"] == "eingang.routen"
    assert res["execution"]["status"] == "COMPLETED"
    assert res["execution"]["output"]["ziel_organisationseinheit"] == "AMT_50_SOZIALES"
    assert await audit.verify_chain("stadt-musterhausen") is True


async def test_lwp_11_use_case_b_antrag_vorpruefen_gruen_gelb_rot() -> None:
    # 1. Korb GRÜN: Alle Pflichtfelder und Nachweise vorhanden
    ctx_g, _, _ = create_default_runtime_context()
    res_gruen = await run_unified_skill_workflow(
        ctx_g,
        text="Bitte führe die Vorprüfung für diesen Wohngeldantrag durch.",
        payload={
            "antrag_formular": {"antragsteller": "Person_1", "anschrift": "Musterstr. 1"},
            "anlagen_texte": ["Gehaltsabrechnung Oktober", "Mietbescheinigung über Kaltmiete"],
        },
    )
    assert res_gruen["detection"]["detected_skill_id"] == "antrag.vorpruefen"
    assert res_gruen["execution"]["output"]["korb"] == "GRUEN"
    assert res_gruen["execution"]["output"]["frist_datum"] is None

    # 2. Korb GELB: Unleserlicher / unsicherer Nachweis -> Vorlage an Sachbearbeitung
    ctx_y, _, _ = create_default_runtime_context()
    res_gelb = await run_unified_skill_workflow(
        ctx_y,
        text="Wohngeldantrag Vorprüfung durchführen.",
        payload={
            "antrag_formular": {"antragsteller": "Person_1", "anschrift": "Musterstr. 1"},
            "anlagen_texte": ["Scan unleserlich und unklar"],
        },
    )
    assert res_gelb["execution"]["output"]["korb"] == "GELB"

    # 3. Korb ROT: Mietnachweis fehlt -> Nachforderung mit §-31-VwVfG-Frist (Sonntag 15.11. -> Montag 16.11.)
    ctx_r, _, _ = create_default_runtime_context()
    res_rot = await run_unified_skill_workflow(
        ctx_r,
        text="Wohngeldantrag auf Vollständigkeit prüfen.",
        payload={
            "antrag_formular": {"antragsteller": "Person_1", "anschrift": "Musterstr. 1"},
            "anlagen_texte": ["Gehaltsabrechnung Oktober"],
            "ereignis_datum": "2026-11-01",
        },
    )
    assert res_rot["execution"]["output"]["korb"] == "ROT"
    assert res_rot["execution"]["output"]["frist_datum"] == "2026-11-16"
    assert "Mietbescheinigung" in res_rot["execution"]["output"]["nachforderung_text"]


async def test_lwp_11_use_case_c_rechtsauskunft_mit_zitatpruefung() -> None:
    ctx, _, _ = create_default_runtime_context()
    res = await run_unified_skill_workflow(
        ctx,
        text="Bitte erstelle eine Rechtsauskunft zur Fristberechnung nach § 31 VwVfG.",
        payload={"fragestellung": "Wann endet eine Frist nach § 31 VwVfG an einem Sonntag?"},
    )
    assert res["detection"]["detected_skill_id"] == "auskunft.recht"
    assert res["execution"]["status"] == "COMPLETED"
    assert res["execution"]["output"]["norm_fundstelle"] == "§ 31 VwVfG"
    assert res["execution"]["output"]["quotes_verified"] is True


async def test_lwp_11_use_case_d_feld_extraktion_mit_zitat() -> None:
    ctx, _, _ = create_default_runtime_context()
    res = await run_unified_skill_workflow(
        ctx,
        text="Extrahiere IBAN und Kaltmiete aus dem Dokument.",
        payload={
            "dokument_text": (
                "Die Überweisung erfolgt auf IBAN DE89370400440532013000. "
                "Die vereinbarte Kaltmiete beträgt 620,00 EUR."
            ),
            "zielfelder": ["iban", "kaltmiete"],
        },
    )
    assert res["detection"]["detected_skill_id"] == "dokument.extrahieren"
    assert res["execution"]["status"] == "COMPLETED"
    felder = res["execution"]["output"]["felder"]
    assert felder["iban"]["wert"] == "DE89370400440532013000"
    assert felder["kaltmiete"]["wert"] == "620,00 EUR"


async def test_lwp_11_use_case_e_ifg_pdf_schwaerzung_hitl_and_completion() -> None:
    # Schritt 1: Ohne Vorab-Bestätigung hält der Skill an Stufe 3 (WAITING_HUMAN) für L1-Freigabe an
    ctx1, approval_store, _ = create_default_runtime_context()
    res_wait = await run_unified_skill_workflow(
        ctx1,
        text="IFG-Anfrage: Bitte Dokument für das Ratsinformationssystem schwärzen.",
        payload={
            "doc_id": "123e4567-e89b-12d3-a456-426614174000",
            "zweck": "ifg_auskunft",
            "dokument_text": "Stellungnahme von Herrn Max Mustermann (DE89370400440532013000).",
        },
    )
    assert res_wait["detection"]["detected_skill_id"] == "dokument.schwaerzen"
    assert res_wait["execution"]["status"] == "WAITING_HUMAN"
    approval_id = res_wait["execution"]["output"]["approval_id"]
    p_hash = res_wait["execution"]["output"]["payload_hash"]

    # Sachbearbeiter gibt in der BITV-Workbench frei
    approval_store.decide(
        approval_id=approval_id,
        decision="APPROVED",
        decided_by="datenschutz-1",
        expected_payload_hash=p_hash,
    )

    # Schritt 2: Fortsetzung nach L1-Freigabe führt physische PDF-Schwärzung mit 0-%-Resttext durch
    ctx2, _, _ = create_default_runtime_context()
    res_done = await run_unified_skill_workflow(
        ctx2,
        text="IFG-Anfrage: Bitte Dokument für das Ratsinformationssystem schwärzen.",
        payload={
            "doc_id": "123e4567-e89b-12d3-a456-426614174000",
            "zweck": "ifg_auskunft",
            "dokument_text": "Stellungnahme von Herrn Max Mustermann (DE89370400440532013000).",
            "human_approved": True,
        },
    )
    assert res_done["execution"]["status"] == "COMPLETED"
    assert res_done["execution"]["output"]["text_layer_clean"] is True
    assert res_done["execution"]["output"]["removed_spans_count"] >= 2
