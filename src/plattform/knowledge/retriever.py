"""Dreipfad-Hybrid-Retriever (P4, LWP-08):
1. Bi-Temporaler Stichtagsfilter (`gueltig_ab <= stichtag <= gueltig_bis`)
2. Pfad A: Dense Embedding über MediaPipe `TextEmbedder`
3. Pfad B: DE-BM25 mit deutscher Kompositazerlegung
4. Pfad C: Deterministischer Exact-Match für Paragrafen/Normen (`§ 31 VwVfG`)
5. Reciprocal Rank Fusion (RRF, k = 60)
6. 1-Hop-Norm-Nachlader (`referenced_norms`)
7. Striktes Top-4-Limit (<= 1.200 Token) für das 4k-Kontextfenster des MediaPipe 4B-Modells
"""

from __future__ import annotations

from datetime import date

import numpy as np

from plattform.knowledge.chunker import LegalChunk, extract_referenced_norms
from plattform.knowledge.de_bm25 import GermanBM25Index
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter


class LightweightHybridRetriever:
    """Dreipfad-Legal-RAG-Retriever mit Stichtagsfilter, RRF (k=60), 1-Hop-Nachlader und Top-4-Cap."""

    def __init__(
        self,
        gateway: MediaPipeGatewayAdapter | None = None,
        rrf_k: int = 60,
        max_top_k: int = 4,
    ) -> None:
        self.gateway = gateway or MediaPipeGatewayAdapter()
        self.rrf_k = rrf_k
        self.max_top_k = max_top_k
        self._chunks: dict[str, LegalChunk] = {}
        self._norm_to_chunk_ids: dict[str, list[str]] = {}
        self._embeddings: dict[str, np.ndarray] = {}
        self._bm25 = GermanBM25Index()

    async def index_chunks(self, chunks: list[LegalChunk]) -> None:
        for ch in chunks:
            self._chunks[ch.chunk_id] = ch
            self._norm_to_chunk_ids.setdefault(ch.norm_id, []).append(ch.chunk_id)
            self._embeddings[ch.chunk_id] = await self.gateway.embed_single(ch.contextualized_text)

        self._bm25.index_documents(
            {cid: c.contextualized_text for cid, c in self._chunks.items()}
        )

    def _valid_at_stichtag(self, chunk: LegalChunk, stichtag: date) -> bool:
        if chunk.gueltig_ab > stichtag:
            return False
        return not (chunk.gueltig_bis is not None and chunk.gueltig_bis < stichtag)

    async def retrieve(
        self,
        query: str,
        stichtag: date | None = None,
        top_k: int = 4,
        expand_1hop: bool = True,
    ) -> list[LegalChunk]:
        effective_date = stichtag or date.today()
        limit = min(top_k, self.max_top_k)

        valid_ids = {
            cid for cid, ch in self._chunks.items() if self._valid_at_stichtag(ch, effective_date)
        }
        if not valid_ids:
            return []

        # Pfad 1: Dense Cosine über MediaPipe TextEmbedder
        q_vec = await self.gateway.embed_single(query)
        dense_scored: list[tuple[str, float]] = []
        for cid in valid_ids:
            emb = self._embeddings[cid]
            sim = float(np.dot(q_vec, emb))
            dense_scored.append((cid, sim))
        dense_scored.sort(key=lambda item: item[1], reverse=True)
        dense_ranks = {cid: rank for rank, (cid, _) in enumerate(dense_scored, start=1)}

        # Pfad 2: DE-BM25 (mit Kompositazerlegung)
        bm25_hits = self._bm25.search(query, allowed_doc_ids=valid_ids, top_k=20)
        bm25_ranks = {cid: rank for rank, (cid, _) in enumerate(bm25_hits, start=1)}

        # Pfad 3: Exact-Match für genannte Paragrafen/Normen
        query_norms = extract_referenced_norms(query)
        exact_matches: list[str] = []
        for cid in valid_ids:
            ch = self._chunks[cid]
            if any(qn.lower() in ch.norm_id.lower() or qn.lower() in ch.raw_text.lower() for qn in query_norms):
                exact_matches.append(cid)
        exact_ranks = {cid: rank for rank, cid in enumerate(exact_matches, start=1)}

        # Reciprocal Rank Fusion (RRF, k = 60)
        rrf_scores: dict[str, float] = {}
        for cid in valid_ids:
            score = 0.0
            if cid in dense_ranks:
                score += 1.0 / (self.rrf_k + dense_ranks[cid])
            if cid in bm25_ranks:
                score += 1.5 / (self.rrf_k + bm25_ranks[cid])
            if cid in exact_ranks:
                score += 2.0 / (self.rrf_k + exact_ranks[cid])
            rrf_scores[cid] = score

        ranked_ids = [
            cid for cid, _ in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        ]

        selected: list[LegalChunk] = []
        seen_ids: set[str] = set()
        for cid in ranked_ids:
            if len(selected) >= limit:
                break
            selected.append(self._chunks[cid])
            seen_ids.add(cid)

        # 1-Hop-Norm-Nachlader: Wenn der Top-Treffer auf eine andere gültige Norm verweist, lade sie nach
        if expand_1hop and selected:
            primary = selected[0]
            for ref_norm in primary.referenced_norms:
                for ref_cid in self._norm_to_chunk_ids.get(ref_norm, []):
                    if ref_cid in valid_ids and ref_cid not in seen_ids:
                        if len(selected) >= limit:
                            selected[-1] = self._chunks[ref_cid]
                        else:
                            selected.append(self._chunks[ref_cid])
                        seen_ids.add(ref_cid)
                        break

        return selected[: self.max_top_k]
