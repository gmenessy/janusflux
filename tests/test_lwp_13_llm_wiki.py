"""Abnahmetests für LWP-13: Strukturiertes LLM-Wiki (Markdown + Frontmatter + SQLite FTS5/BM25)
als Ersatz für Qdrant & Dense-Embeddings.
Prüft alle vier Architektur-Säulen:
1. Wissens- & Rechtsauskunft (Use Case C) über das LLM-Wiki für Schutzklassen 0 & 1 inkl. VAL-007.
2. Nativer 1-Hop-Verweisgraph über explizite [[Wiki-Links]] und bi-temporale Stichtagsfilterung in SQLite.
3. Skills als Wiki-Knoten (`wiki/skills/*.md`) für schnelles deterministisches Volltext-Routing im SkillDetector.
4. Harte Grenze bei Einzelfallakten (`WikiSecurityBoundaryError` bei Schutzklasse >= 2 oder PII)
   sowie verpflichtende Vor-/Nachschaltung von PII-Tresor, DMN-Engine und HITL-Freigabe bei Einzelfällen.
"""

from datetime import date
from pathlib import Path

import pytest

from plattform.knowledge.llm_wiki import LLMWikiEngine, WikiSecurityBoundaryError
from plattform.skills.registry import SkillRegistry
from workflows.unified_skill_router import (
    create_default_runtime_context,
    run_unified_skill_workflow,
)


def test_lwp_13_wiki_loads_markdown_frontmatter_and_resolves_native_1hop_links() -> None:
    wiki = LLMWikiEngine(Path("wiki"))

    # Suche nach Sondernutzungsgebühren (Schutzklasse 1 Satzung) -> verweist per [[§ 31 VwVfG]] auf § 31 VwVfG
    results = wiki.search_norms(
        query="Wie lautet die Satzungsregelung zur Sondernutzungsgebühr?",
        stichtag=date(2026, 11, 15),
        top_k=4,
        expand_1hop=True,
    )
    norms = [c.norm_id for c in results]
    assert "§ 4 Sondernutzungssatzung" in norms
    # Nativer 1-Hop-Verweisgraph über [[§ 31 VwVfG]] im Markdown-Text lädt § 31 VwVfG automatisch mit
    assert "§ 31 VwVfG" in norms
    # Aufgehobene Altfassung 2010 (gueltig_bis: 2019-12-31) wird durch SQLite-Stichtagsfilter ausgeschlossen
    assert "§ 31 VwVfG (Altfassung 2010)" not in norms
    # [[Wiki-Link]]-Klammern sind im raw_text für die wörtliche Zitatprüfung (VAL-007) bereinigt
    assert "[[" not in results[0].raw_text


def test_lwp_13_skills_as_wiki_nodes_route_deterministically_via_sqlite_fts5() -> None:
    registry = SkillRegistry(Path("skills"), wiki_dir=Path("wiki"))
    registry.load_all()
    all_skills = registry.list_all()

    top_schwaerzen = registry.top_k_by_wiki_fts(
        "Bitte Akte für das Ratsinformationssystem anonymisieren", all_skills, k=3
    )
    assert top_schwaerzen[0] == "dokument.schwaerzen"

    top_auskunft = registry.top_k_by_wiki_fts(
        "Satzungsauskunft zur Sondernutzungssatzung erstellen", all_skills, k=3
    )
    assert top_auskunft[0] == "auskunft.recht"


def test_lwp_13_enforces_hard_boundary_against_case_files_and_pii() -> None:
    # 1. Schutzklasse >= 2 (Einzelfallakte) wird strikt abgewiesen
    case_file_md = """---
node_id: "akte:wohngeld_2026_104"
node_type: "norm"
norm_id: "Einzelfallakte 104"
schutzklasse: 2
gueltig_ab: "2026-01-01"
---
Bescheid-Entwurf im Einzelfallverfahren.
"""
    with pytest.raises(WikiSecurityBoundaryError, match="Schutzklasse 2 > 1"):
        LLMWikiEngine.parse_markdown_node(case_file_md, source_path="einzelfall.md")

    # 2. Personenbezogene Daten (z. B. IBAN oder Klartextname) trotz Schutzklasse 0 werden strikt abgewiesen
    pii_leak_md = """---
node_id: "norm:versteckter_einzelfall"
node_type: "norm"
norm_id: "Dienstanweisung"
schutzklasse: 0
gueltig_ab: "2026-01-01"
---
Auszahlung an Herrn Max Mustermann auf Konto DE89370400440532013000.
"""
    with pytest.raises(WikiSecurityBoundaryError, match="Personenbezogene Daten"):
        LLMWikiEngine.parse_markdown_node(pii_leak_md, source_path="pii_leak.md")


async def test_lwp_13_use_case_c_wiki_auskunft_and_case_file_guards_remain_intact() -> None:
    # Use Case C (Schutzklasse 1): Satzungsauskunft läuft komplett über das LLM-Wiki + VAL-007 Zitat-Gate
    ctx_c, _, _ = create_default_runtime_context(schutzklasse=1)
    res_c = await run_unified_skill_workflow(
        ctx_c,
        text="Bitte erstelle eine Satzungsauskunft zur Sondernutzungsgebühr.",
        payload={"fragestellung": "Wie werden Gebühren nach § 4 Sondernutzungssatzung erhoben?"},
    )
    assert res_c["detection"]["detected_skill_id"] == "auskunft.recht"
    assert res_c["execution"]["status"] == "COMPLETED"
    assert res_c["execution"]["output"]["norm_fundstelle"] == "§ 4 Sondernutzungssatzung"
    assert res_c["execution"]["output"]["quotes_verified"] is True

    # Einzelfallakte (Use Case B, Schutzklasse 2): PII-Tresor, DMN-Engine und §-31-VwVfG-Frist bleiben aktiv
    ctx_b, _, _ = create_default_runtime_context(schutzklasse=2)
    res_b = await run_unified_skill_workflow(
        ctx_b,
        text="Wohngeldantrag auf Vollständigkeit prüfen.",
        payload={
            "antrag_formular": {"antragsteller": "Person_1", "anschrift": "Musterweg 2"},
            "anlagen_texte": ["Einkommensnachweis vorhanden"],
            "ereignis_datum": "2026-11-01",
        },
    )
    assert res_b["execution"]["output"]["korb"] == "ROT"
    assert res_b["execution"]["output"]["frist_datum"] == "2026-11-16"
