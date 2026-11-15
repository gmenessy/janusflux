"""Strukturiertes LLM-Wiki auf Basis von Markdown-Frontmatter, nativen [[Wiki-Links]]
und lokaler SQLite-FTS5/BM25-Volltextsuche (P4 Ultra-Lightweight, ersetzt Qdrant & Dense-Embeddings).

Architektur-Garantien:
1. Wissens- & Rechtsauskunft (Use Case C, Schutzklasse 0 & 1): Dateibasiertes Markdown-Wiki
   mit YAML-Frontmatter und SQLite FTS5 (`bm25()`) inkl. deutscher Kompositazerlegung.
2. Nativer Verweisgraph: Explizite `[[Wiki-Links]]` und Frontmatter-`links` bilden den
   1-Hop-Normgraphen und die bi-temporale Stichtagsfilterung direkt in SQLite ab.
3. Skills als Wiki-Knoten: Jeder Skill wird samt Trigger-Begriffen, Schema-Verweis und
   DMN-Regel als eigener Wiki-Knoten in SQLite FTS5 indiziert; `SkillDetector` routet
   per Volltextsuche statt über Embeddings.
4. Harte Grenze bei Einzelfallakten (`WikiSecurityBoundaryError`): Dokumente der
   Schutzklassen >= 2 oder Texte mit personenbezogenen Daten (IBAN, Steuer-ID, E-Mail,
   Klartextnamen) dürfen NIEMALS in das LLM-Wiki aufgenommen werden.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from plattform.knowledge.chunker import LegalChunk, extract_referenced_norms
from plattform.knowledge.de_bm25 import tokenize_de_bm25
from plattform.policy_privacy.pii_detectors import detect_all_pii_spans

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


class WikiSecurityBoundaryError(PermissionError):
    """Wird ausgelöst, wenn versucht wird, Einzelfallakten (Schutzklasse >= 2) oder PII im Wiki zu speichern."""


@dataclass(frozen=True)
class WikiNode:
    node_id: str
    node_type: str  # "norm" | "skill"
    title: str
    schutzklasse: int
    body_markdown: str
    clean_text: str
    norm_id: str = ""
    gesetz_kurz: str = ""
    skill_id: str = ""
    dmn_table_id: str = ""
    gueltig_ab: date = date(1970, 1, 1)
    gueltig_bis: date | None = None
    links: list[str] = field(default_factory=list)
    trigger_keywords: list[str] = field(default_factory=list)


def strip_wiki_link_brackets(text: str) -> str:
    """Wandelt `[[§ 32 VwVfG]]` in `§ 32 VwVfG` für saubere Zitat-Prüfung (VAL-007) um."""
    return WIKI_LINK_RE.sub(r"\1", text)


class LLMWikiEngine:
    """Lokale SQLite-FTS5/BM25 Wiki-Engine für Rechtsnormen (SK 0/1) und Skill-Knoten."""

    def __init__(self, wiki_dir: Path = Path("wiki"), db_path: str = ":memory:") -> None:
        self.wiki_dir = wiki_dir
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._nodes: dict[str, WikiNode] = {}
        self._init_schema()
        if self.wiki_dir.exists():
            self.load_directory(self.wiki_dir)

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS wiki_nodes (
                node_id       TEXT PRIMARY KEY,
                node_type     TEXT NOT NULL CHECK (node_type IN ('norm', 'skill')),
                title         TEXT NOT NULL,
                schutzklasse  INTEGER NOT NULL CHECK (schutzklasse IN (0, 1)),
                norm_id       TEXT NOT NULL DEFAULT '',
                gesetz_kurz   TEXT NOT NULL DEFAULT '',
                skill_id      TEXT NOT NULL DEFAULT '',
                dmn_table_id  TEXT NOT NULL DEFAULT '',
                gueltig_ab    TEXT NOT NULL,
                gueltig_bis   TEXT,
                clean_text    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_edges (
                source_node_id TEXT NOT NULL,
                target_ref     TEXT NOT NULL,
                PRIMARY KEY (source_node_id, target_ref)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
                node_id UNINDEXED,
                node_type UNINDEXED,
                title,
                norm_or_skill_id,
                clean_text,
                de_compound_tokens,
                tokenize='unicode61'
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def parse_markdown_node(raw_markdown: str, source_path: str = "<memory>") -> WikiNode:
        match = FRONTMATTER_RE.match(raw_markdown.strip())
        if not match:
            raise ValueError(f"Wiki-Datei '{source_path}' besitzt keinen gültigen YAML-Frontmatter-Header.")
        meta_raw, body_md = match.group(1), match.group(2).strip()
        meta: dict[str, Any] = yaml.safe_load(meta_raw) or {}

        schutzklasse = int(meta.get("schutzklasse", 0))
        if schutzklasse > 1:
            raise WikiSecurityBoundaryError(
                f"Einzelfallakten-Schranke verletzt in '{source_path}': "
                f"Schutzklasse {schutzklasse} > 1 darf nicht in das LLM-Wiki geladen werden."
            )

        clean_body = strip_wiki_link_brackets(body_md)
        pii_spans = detect_all_pii_spans(clean_body)
        if pii_spans:
            raise WikiSecurityBoundaryError(
                f"Einzelfallakten-Schranke verletzt in '{source_path}': "
                f"Personenbezogene Daten ({pii_spans[0].entity_type}: '{pii_spans[0].text}') im Wiki verboten."
            )

        inline_links = [m.group(1).strip() for m in WIKI_LINK_RE.finditer(body_md)]
        fm_links = [str(x).strip() for x in (meta.get("links") or [])]
        norm_id = str(meta.get("norm_id", ""))
        auto_refs = extract_referenced_norms(clean_body, self_norm_id=norm_id)

        merged_links: list[str] = []
        for ref in fm_links + inline_links + auto_refs:
            if ref and ref != norm_id and ref not in merged_links:
                merged_links.append(ref)

        g_ab_raw = meta.get("gueltig_ab")
        g_ab = date.fromisoformat(str(g_ab_raw)) if g_ab_raw else date(1970, 1, 1)
        g_bis_raw = meta.get("gueltig_bis")
        g_bis = date.fromisoformat(str(g_bis_raw)) if g_bis_raw else None

        return WikiNode(
            node_id=str(meta["node_id"]),
            node_type=str(meta.get("node_type", "norm")),
            title=str(meta.get("titel", meta.get("title", meta["node_id"]))),
            schutzklasse=schutzklasse,
            body_markdown=body_md,
            clean_text=clean_body,
            norm_id=norm_id,
            gesetz_kurz=str(meta.get("gesetz_kurz", "")),
            skill_id=str(meta.get("skill_id", "")),
            dmn_table_id=str(meta.get("dmn_table_id", "")),
            gueltig_ab=g_ab,
            gueltig_bis=g_bis,
            links=merged_links,
            trigger_keywords=[str(k) for k in (meta.get("trigger_keywords") or [])],
        )

    def upsert_node(self, node: WikiNode) -> None:
        if node.schutzklasse > 1:
            raise WikiSecurityBoundaryError(
                f"Knoten '{node.node_id}' hat Schutzklasse {node.schutzklasse} (nur 0 und 1 im Wiki erlaubt)."
            )
        pii_spans = detect_all_pii_spans(node.clean_text)
        if pii_spans:
            raise WikiSecurityBoundaryError(
                f"Knoten '{node.node_id}' enthält personenbezogene Daten ({pii_spans[0].entity_type})."
            )

        self._nodes[node.node_id] = node
        compound_Bag = " ".join(
            tokenize_de_bm25(
                f"{node.title} {node.norm_id} {node.skill_id} "
                f"{' '.join(node.trigger_keywords)} {node.clean_text}"
            )
        )
        cur = self._conn.cursor()
        cur.execute("DELETE FROM wiki_nodes WHERE node_id = ?", (node.node_id,))
        cur.execute("DELETE FROM wiki_edges WHERE source_node_id = ?", (node.node_id,))
        cur.execute("DELETE FROM wiki_fts WHERE node_id = ?", (node.node_id,))

        cur.execute(
            """
            INSERT INTO wiki_nodes (
                node_id, node_type, title, schutzklasse, norm_id, gesetz_kurz,
                skill_id, dmn_table_id, gueltig_ab, gueltig_bis, clean_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.node_id,
                node.node_type,
                node.title,
                node.schutzklasse,
                node.norm_id,
                node.gesetz_kurz,
                node.skill_id,
                node.dmn_table_id,
                node.gueltig_ab.isoformat(),
                node.gueltig_bis.isoformat() if node.gueltig_bis else None,
                node.clean_text,
            ),
        )
        for ref in node.links:
            cur.execute(
                "INSERT OR IGNORE INTO wiki_edges (source_node_id, target_ref) VALUES (?, ?)",
                (node.node_id, ref),
            )
        cur.execute(
            """
            INSERT INTO wiki_fts (
                node_id, node_type, title, norm_or_skill_id, clean_text, de_compound_tokens
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                node.node_id,
                node.node_type,
                node.title,
                node.norm_id or node.skill_id,
                node.clean_text,
                compound_Bag,
            ),
        )
        self._conn.commit()

    def load_directory(self, directory: Path) -> int:
        count = 0
        for md_path in sorted(directory.rglob("*.md")):
            raw = md_path.read_text(encoding="utf-8")
            node = self.parse_markdown_node(raw, source_path=str(md_path))
            self.upsert_node(node)
            count += 1
        return count

    @staticmethod
    def _build_fts_match_expression(query: str) -> str:
        tokens = tokenize_de_bm25(query)
        safe_tokens = [re.sub(r"[^a-z0-9äöüß]", "", t) for t in tokens]
        safe_tokens = [t for t in safe_tokens if len(t) >= 2]
        if not safe_tokens:
            return ""
        unique_tokens = list(dict.fromkeys(safe_tokens))[:16]
        return " OR ".join(f'"{t}"' for t in unique_tokens)

    def _node_to_legal_chunk(self, node: WikiNode) -> LegalChunk:
        header = (
            f"[Wiki-Knoten: {node.node_id} | Quelle: {node.gesetz_kurz} | "
            f"Norm: {node.norm_id} | Titel: {node.title} | "
            f"Gültig ab: {node.gueltig_ab.isoformat()}]"
        )
        contextualized = f"{header}\n{node.clean_text}"
        return LegalChunk(
            chunk_id=node.node_id,
            doc_id=node.node_id,
            gesetz_kurz=node.gesetz_kurz,
            norm_id=node.norm_id,
            titel=node.title,
            context_header=header,
            raw_text=node.clean_text,
            contextualized_text=contextualized,
            token_count=len(contextualized.split()),
            gueltig_ab=node.gueltig_ab,
            gueltig_bis=node.gueltig_bis,
            referenced_norms=list(node.links),
        )

    def search_norms(
        self,
        query: str,
        stichtag: date | None = None,
        top_k: int = 4,
        expand_1hop: bool = True,
    ) -> list[LegalChunk]:
        """Durchsucht das LLM-Wiki per SQLite FTS5 (BM25) + Paragrafen-Exact-Match
        mit bi-temporalem Stichtagsfilter und nativer 1-Hop-Wiki-Link-Auflösung.
        """
        effective_date = (stichtag or date.today()).isoformat()
        cur = self._conn.cursor()

        # 1. Alle zum Stichtag gültigen Norm-Knoten bestimmen
        valid_rows = cur.execute(
            """
            SELECT node_id, norm_id FROM wiki_nodes
            WHERE node_type = 'norm'
              AND gueltig_ab <= ?
              AND (gueltig_bis IS NULL OR gueltig_bis >= ?)
            """,
            (effective_date, effective_date),
        ).fetchall()
        valid_ids = {str(r["node_id"]) for r in valid_rows}
        if not valid_ids:
            return []

        scores: dict[str, float] = {}

        # 2. Deterministischer Exact-Match auf Paragrafen-Referenzen in der Anfrage
        query_norms = extract_referenced_norms(query)
        for r in valid_rows:
            nid = str(r["node_id"])
            norm_id_str = str(r["norm_id"])
            if any(qn.lower() in norm_id_str.lower() for qn in query_norms):
                scores[nid] = scores.get(nid, 0.0) + 50.0

        # 3. SQLite FTS5 BM25 Volltextsuche (inkl. deutscher Kompositazerlegung)
        fts_expr = self._build_fts_match_expression(query)
        if fts_expr:
            fts_rows = cur.execute(
                """
                SELECT node_id, bm25(wiki_fts, 0.0, 0.0, 3.0, 5.0, 1.5, 2.0) AS rank_score
                FROM wiki_fts
                WHERE wiki_fts MATCH ? AND node_type = 'norm'
                ORDER BY rank_score ASC
                LIMIT 20
                """,
                (fts_expr,),
            ).fetchall()
            for rank_idx, row in enumerate(fts_rows, start=1):
                nid = str(row["node_id"])
                if nid in valid_ids:
                    # bm25() in SQLite FTS5 ist negativ (je kleiner, desto besser)
                    bm25_val = abs(float(row["rank_score"]))
                    scores[nid] = scores.get(nid, 0.0) + bm25_val + (10.0 / rank_idx)

        ranked_ids = [
            nid for nid, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ]
        if not ranked_ids:
            ranked_ids = sorted(valid_ids)

        selected: list[LegalChunk] = []
        seen_ids: set[str] = set()
        for nid in ranked_ids:
            if len(selected) >= top_k:
                break
            selected.append(self._node_to_legal_chunk(self._nodes[nid]))
            seen_ids.add(nid)

        # 4. Nativer 1-Hop-Verweisgraph über wiki_edges (explizite [[Wiki-Links]])
        if expand_1hop and selected:
            primary_id = selected[0].chunk_id
            edge_rows = cur.execute(
                "SELECT target_ref FROM wiki_edges WHERE source_node_id = ?",
                (primary_id,),
            ).fetchall()
            for edge in edge_rows:
                target_ref = str(edge["target_ref"])
                target_rows = cur.execute(
                    """
                    SELECT node_id FROM wiki_nodes
                    WHERE node_type = 'norm'
                      AND (norm_id = ? OR node_id = ?)
                      AND gueltig_ab <= ?
                      AND (gueltig_bis IS NULL OR gueltig_bis >= ?)
                    """,
                    (target_ref, target_ref, effective_date, effective_date),
                ).fetchall()
                for tr in target_rows:
                    t_nid = str(tr["node_id"])
                    if t_nid in valid_ids and t_nid not in seen_ids:
                        chunk = self._node_to_legal_chunk(self._nodes[t_nid])
                        if len(selected) >= top_k:
                            selected[-1] = chunk
                        else:
                            selected.append(chunk)
                        seen_ids.add(t_nid)
                        break

        return selected[:top_k]

    def rank_skills(
        self,
        query: str,
        allowed_skill_ids: set[str],
        top_k: int = 5,
    ) -> list[str]:
        """Routet und sortiert Kandidaten-Skills schnell und deterministisch über SQLite FTS5 (BM25)
        statt über Dense-Embeddings.
        """
        if not allowed_skill_ids:
            return []
        fts_expr = self._build_fts_match_expression(query)
        scored: dict[str, float] = {sid: 0.0 for sid in allowed_skill_ids}

        if fts_expr:
            cur = self._conn.cursor()
            rows = cur.execute(
                """
                SELECT w.skill_id, bm25(wiki_fts, 0.0, 0.0, 3.0, 4.0, 1.5, 2.5) AS rank_score
                FROM wiki_fts
                JOIN wiki_nodes w ON w.node_id = wiki_fts.node_id
                WHERE wiki_fts MATCH ? AND wiki_fts.node_type = 'skill'
                ORDER BY rank_score ASC
                """,
                (fts_expr,),
            ).fetchall()
            for rank_idx, r in enumerate(rows, start=1):
                sid = str(r["skill_id"])
                if sid in scored:
                    scored[sid] += abs(float(r["rank_score"])) + (20.0 / rank_idx)

        ranked = [
            sid for sid, _ in sorted(scored.items(), key=lambda item: item[1], reverse=True)
        ]
        return ranked[:top_k]
