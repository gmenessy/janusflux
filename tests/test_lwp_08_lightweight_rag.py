"""Abnahmetests für LWP-08: Lightweight Contextual Legal RAG (P4 – 300-Token-Chunker, DE-BM25 Komposita, 1-Hop-Graph & RRF)."""

from datetime import date

from plattform.knowledge.chunker import LightweightLegalChunker
from plattform.knowledge.de_bm25 import GermanBM25Index, tokenize_de_bm25
from plattform.knowledge.retriever import LightweightHybridRetriever


def test_lwp_08_chunker_preserves_paragraphs_and_prepends_context() -> None:
    statute_text = """
§ 31 Fristen und Termine
(1) Für die Berechnung von Fristen und für die Bestimmung von Terminen gelten die §§ 187 bis 193 des Bürgerlichen Gesetzbuches entsprechend, soweit nicht durch § 32 VwVfG etwas anderes bestimmt ist.
(3) Fällt das Ende einer Frist auf einen Sonntag, einen gesetzlichen Feiertag oder einen Sonnabend, so endet die Frist mit dem Ablauf des nächstfolgenden Werktages.

§ 32 Wiedereinsetzung in den vorigen Stand
(1) War jemand ohne Verschulden verhindert, eine gesetzliche Frist einzuhalten, so ist ihm auf Antrag Wiedereinsetzung in den vorigen Stand zu gewähren.
"""
    chunker = LightweightLegalChunker(max_chunk_tokens=280)
    chunks = chunker.chunk_statute(doc_id="vwvfg", gesetz_kurz="VwVfG", text=statute_text)
    assert len(chunks) == 2
    assert chunks[0].norm_id == "§ 31 VwVfG"
    assert chunks[1].norm_id == "§ 32 VwVfG"
    assert "[Quelle: VwVfG | Norm: § 31 VwVfG" in chunks[0].contextualized_text
    assert "§ 32 VwVfG" in chunks[0].referenced_norms
    assert all(c.token_count <= 300 for c in chunks)


def test_lwp_08_de_bm25_compound_splitting_matches_antrag() -> None:
    tokens = tokenize_de_bm25("Wohngeldantragsformular")
    assert "antrag" in tokens
    assert "wohngeld" in tokens
    assert "formular" in tokens

    bm25 = GermanBM25Index()
    bm25.index_documents(
        {
            "d1": "Bitte reichen Sie das Wohngeldantragsformular fristgerecht ein.",
            "d2": "Die Straßenreinigungssatzung regelt den Winterdienst.",
        }
    )
    hits = bm25.search("Antrag")
    assert len(hits) == 1
    assert hits[0][0] == "d1"


async def test_lwp_08_retriever_bitemporal_filter_and_1hop_norm_expansion() -> None:
    chunker = LightweightLegalChunker()
    active_chunks = chunker.chunk_statute(
        doc_id="vwvfg_akt",
        gesetz_kurz="VwVfG",
        text=(
            "§ 31 Fristen\nDie Frist richtet sich ergänzend nach § 32 VwVfG.\n\n"
            "§ 32 Wiedereinsetzung\nBei unverschuldeter Fristversäumnis wird Wiedereinsetzung gewährt."
        ),
        gueltig_ab=date(2020, 1, 1),
        gueltig_bis=None,
    )
    repealed_chunks = chunker.chunk_statute(
        doc_id="vwvfg_alt",
        gesetz_kurz="VwVfG",
        text="§ 31 Alte Fassung\nAufgehobene Regelung aus dem Jahr 2010.",
        gueltig_ab=date(2010, 1, 1),
        gueltig_bis=date(2019, 12, 31),
    )

    retriever = LightweightHybridRetriever()
    await retriever.index_chunks(active_chunks + repealed_chunks)

    results = await retriever.retrieve(
        query="Wie werden Fristen nach § 31 VwVfG berechnet?",
        stichtag=date(2026, 11, 15),
        top_k=4,
        expand_1hop=True,
    )
    returned_ids = [c.chunk_id for c in results]
    returned_norms = [c.norm_id for c in results]

    # Aufgehobene Norm darf nicht enthalten sein
    assert all(cid != "vwvfg_alt::p1" for cid in returned_ids)
    # § 31 VwVfG (Direkttreffer) und § 32 VwVfG (per 1-Hop-Graph nachgeladen) sind enthalten
    assert "§ 31 VwVfG" in returned_norms
    assert "§ 32 VwVfG" in returned_norms
    assert len(results) <= 4
