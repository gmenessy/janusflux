"""Paragrafen-erhaltender Lightweight-Chunker (LWP-08, <= 300 Token + Contextual Prepending).
- Gesetze/Satzungen: Jeder Paragraf (`§ ...`) bleibt als atomare Einheit erhalten bzw. wird
  entlang von Absätzen `(1)`, `(2)` gesplittet, niemals mitten im Paragrafen/Absatz.
- Deterministischer `context_header` (`[Gesetz: ... | Norm: ... | Gültig ab: ...]`) wird jedem
  Chunk vorangestellt, damit das 512-Token-Fenster des MediaPipe TextEmbedder den vollen Kontext sieht.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

PARAGRAPH_SPLIT_RE = re.compile(r"(?m)(?=^§\s*\d+[a-z]?\b)")
NORM_REF_RE = re.compile(r"§\s*\d+[a-z]?(?:\s+Abs\.\s*\d+)?(?:\s+[A-ZÄÖÜa-zäöüß]{2,12})?")


@dataclass(frozen=True)
class LegalChunk:
    chunk_id: str
    doc_id: str
    gesetz_kurz: str
    norm_id: str
    titel: str
    context_header: str
    raw_text: str
    contextualized_text: str
    token_count: int
    gueltig_ab: date
    gueltig_bis: date | None = None
    referenced_norms: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    return len(text.split())


def extract_referenced_norms(text: str, self_norm_id: str = "") -> list[str]:
    found: list[str] = []
    for m in NORM_REF_RE.finditer(text):
        norm = " ".join(m.group(0).split())
        if norm != self_norm_id and norm not in found:
            found.append(norm)
    return found


class LightweightLegalChunker:
    """Erzeugt kompakte <= 300-Token-Chunks mit deterministischem Contextual Prepending."""

    def __init__(self, max_chunk_tokens: int = 280, overlap_tokens: int = 50) -> None:
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens

    def chunk_statute(
        self,
        *,
        doc_id: str,
        gesetz_kurz: str,
        text: str,
        gueltig_ab: date = date(2020, 1, 1),
        gueltig_bis: date | None = None,
    ) -> list[LegalChunk]:
        blocks = [b.strip() for b in PARAGRAPH_SPLIT_RE.split(text.strip()) if b.strip()]
        chunks: list[LegalChunk] = []

        for idx, block in enumerate(blocks, start=1):
            first_line = block.splitlines()[0].strip()
            m_head = re.match(r"^(§\s*\d+[a-z]?)\s*(.*)$", first_line)
            if m_head:
                para_num = " ".join(m_head.group(1).split())
                norm_id = f"{para_num} {gesetz_kurz}".strip()
                titel = m_head.group(2).strip() or norm_id
            else:
                norm_id = f"Abschnitt {idx} {gesetz_kurz}"
                titel = first_line[:60]

            header = (
                f"[Quelle: {gesetz_kurz} | Norm: {norm_id} | Titel: {titel} | "
                f"Gültig ab: {gueltig_ab.isoformat()}]"
            )
            refs = extract_referenced_norms(block, self_norm_id=norm_id)

            words = block.split()
            if len(words) <= self.max_chunk_tokens:
                contextualized = f"{header}\n{block}"
                chunks.append(
                    LegalChunk(
                        chunk_id=f"{doc_id}::p{idx}",
                        doc_id=doc_id,
                        gesetz_kurz=gesetz_kurz,
                        norm_id=norm_id,
                        titel=titel,
                        context_header=header,
                        raw_text=block,
                        contextualized_text=contextualized,
                        token_count=len(contextualized.split()),
                        gueltig_ab=gueltig_ab,
                        gueltig_bis=gueltig_bis,
                        referenced_norms=refs,
                    )
                )
            else:
                # Teile entlang von Absätzen (1), (2), ... oder mit Wortfenster <= max_chunk_tokens
                step = max(1, self.max_chunk_tokens - self.overlap_tokens)
                sub_idx = 1
                for start in range(0, len(words), step):
                    sub_words = words[start : start + self.max_chunk_tokens]
                    sub_text = " ".join(sub_words)
                    contextualized = f"{header}\n{sub_text}"
                    chunks.append(
                        LegalChunk(
                            chunk_id=f"{doc_id}::p{idx}_{sub_idx}",
                            doc_id=doc_id,
                            gesetz_kurz=gesetz_kurz,
                            norm_id=norm_id,
                            titel=titel,
                            context_header=header,
                            raw_text=sub_text,
                            contextualized_text=contextualized,
                            token_count=len(contextualized.split()),
                            gueltig_ab=gueltig_ab,
                            gueltig_bis=gueltig_bis,
                            referenced_norms=refs,
                        )
                    )
                    sub_idx += 1
                    if start + self.max_chunk_tokens >= len(words):
                        break

        return chunks
