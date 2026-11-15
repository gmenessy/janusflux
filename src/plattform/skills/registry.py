"""SkillRegistry mit statischem Ketten-Linter, LLM-Wiki-Knoten-Index (SQLite FTS5) und Embedding-Fallback."""

import re
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from plattform.knowledge.llm_wiki import LLMWikiEngine, WikiNode
from plattform.skills.models import SkillManifest


class SkillChainLintError(ValueError):
    """Wird geworfen, wenn eine Skill-Definition gegen die Eiserne Stufenregel verstößt."""


class SkillRegistry:
    """Lädt, prüft und indiziert alle deklarativen Skills beim Start der Plattform."""

    def __init__(self, skills_dir: Path = Path("skills"), wiki_dir: Path = Path("wiki")) -> None:
        self.skills_dir = skills_dir
        self.wiki_dir = wiki_dir
        self._skills: dict[str, SkillManifest] = {}
        self._compiled_triggers: dict[str, list[re.Pattern[str]]] = {}
        self._skill_embeddings: dict[str, np.ndarray] = {}
        self.wiki_engine = LLMWikiEngine(wiki_dir=wiki_dir)

    def register(self, manifest: SkillManifest, embedder_fn: Any | None = None) -> None:
        self._lint_manifest(manifest)
        self._skills[manifest.skill_id] = manifest
        self._compiled_triggers[manifest.skill_id] = [
            re.compile(pat, re.IGNORECASE) for pat in manifest.trigger_patterns
        ]
        node_id = f"skill:{manifest.skill_id}"
        if node_id not in self.wiki_engine._nodes:
            body_text = f"{manifest.name}. {manifest.beschreibung} " + " ".join(manifest.beispiele)
            self.wiki_engine.upsert_node(
                WikiNode(
                    node_id=node_id,
                    node_type="skill",
                    title=manifest.name,
                    schutzklasse=0,
                    body_markdown=body_text,
                    clean_text=body_text,
                    skill_id=manifest.skill_id,
                    dmn_table_id="skill_routing",
                    gueltig_ab=date(2020, 1, 1),
                    trigger_keywords=list(manifest.beispiele),
                )
            )
        if embedder_fn is not None:
            index_text = (
                f"{manifest.name}. {manifest.beschreibung} "
                + " ".join(manifest.beispiele)
            )
            self._skill_embeddings[manifest.skill_id] = embedder_fn(index_text)

    def load_all(self, embedder_fn: Any | None = None) -> None:
        if not self.skills_dir.exists():
            return
        for manifest_path in sorted(self.skills_dir.rglob("skill.yaml")):
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = SkillManifest.model_validate(raw)
            self.register(manifest, embedder_fn=embedder_fn)

    @staticmethod
    def _lint_manifest(manifest: SkillManifest) -> None:
        if not manifest.steps:
            raise SkillChainLintError(f"Skill '{manifest.skill_id}' enthält keine Schritte.")

        llm_steps = [s for s in manifest.steps if s.stufe == 2]
        if len(llm_steps) > manifest.budget.llm_aufrufe:
            raise SkillChainLintError(
                f"Skill '{manifest.skill_id}' deklariert {len(llm_steps)} Stufe-2-Schritte, "
                f"aber Budget erlaubt nur {manifest.budget.llm_aufrufe}."
            )

        # Eiserne Regel 3 (Abwärtsprüfung): Auf jeden Stufe-2-Schritt muss ein Stufe-0- oder Stufe-1-Prüfschritt folgen
        for idx, step in enumerate(manifest.steps):
            if step.stufe == 2:
                remaining = manifest.steps[idx + 1 :]
                if not any(r.stufe in (0, 1) for r in remaining):
                    raise SkillChainLintError(
                        f"Skill '{manifest.skill_id}': Auf Stufe-2-Schritt '{step.step_id}' "
                        "folgt keine deterministische Abwärtsprüfung (Stufe 0/1)."
                    )

        # Eiserne Regel 5: Irreversible Skills erfordern zwingend einen Stufe-3-Freigabeschritt
        if manifest.wirkung == "irreversible" and not any(s.stufe == 3 for s in manifest.steps):
            raise SkillChainLintError(
                f"Skill '{manifest.skill_id}' ist 'irreversible', enthält aber keinen Stufe-3-Freigabeschritt."
            )

    def get(self, skill_id: str) -> SkillManifest:
        if skill_id not in self._skills:
            raise KeyError(f"Unbekannter Skill: '{skill_id}'")
        return self._skills[skill_id]

    def list_all(self) -> list[SkillManifest]:
        return list(self._skills.values())

    def list_allowed(self, allowed_skill_ids: set[str], schutzklasse: int) -> list[SkillManifest]:
        return [
            s
            for sid, s in self._skills.items()
            if sid in allowed_skill_ids and schutzklasse <= s.max_schutzklasse
        ]

    def match_triggers(self, text: str, allowed_skills: list[SkillManifest]) -> list[str]:
        matched: list[str] = []
        for skill in allowed_skills:
            patterns = self._compiled_triggers.get(skill.skill_id, [])
            if any(p.search(text) for p in patterns):
                matched.append(skill.skill_id)
        return matched

    def top_k_by_wiki_fts(
        self, query_text: str, allowed_skills: list[SkillManifest], k: int = 5
    ) -> list[str]:
        """Stufe 0.5 Vorauswahl über die SQLite-FTS5/BM25-Volltextsuche der Skill-Wiki-Knoten."""
        allowed_ids = {s.skill_id for s in allowed_skills}
        return self.wiki_engine.rank_skills(query_text, allowed_skill_ids=allowed_ids, top_k=k)

    def top_k_by_embedding(
        self, query_vec: np.ndarray, allowed_skills: list[SkillManifest], k: int = 5
    ) -> list[str]:
        if not self._skill_embeddings:
            return [s.skill_id for s in allowed_skills[:k]]
        scored: list[tuple[float, str]] = []
        q_norm = float(np.linalg.norm(query_vec)) or 1.0
        for skill in allowed_skills:
            emb = self._skill_embeddings.get(skill.skill_id)
            if emb is None:
                scored.append((0.0, skill.skill_id))
                continue
            s_norm = float(np.linalg.norm(emb)) or 1.0
            cosine = float(np.dot(query_vec, emb) / (q_norm * s_norm))
            scored.append((cosine, skill.skill_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [sid for _, sid in scored[:k]]
