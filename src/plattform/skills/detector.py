"""Vierstufiger Skill-Detektor (`SkillDetector`, LWP-06) gemäß Abschnitt 2.4 der Spezifikation."""

from typing import Any

from plattform.skills.models import SkillDetectionResult, SkillManifest
from plattform.skills.registry import SkillRegistry


class SkillDetector:
    """Vier-Stufen-Skill-Detektor:
    Stufe 0 (Explizit / Regex-Trigger / Pflichtfeld-Check)
    -> Stufe 0.5 (MediaPipe Embedding Top-K Vorauswahl)
    -> Stufe 1 (MediaPipe Decision Contract 'skill.detector' mit CRC-Schwelle)
    -> Stufe 0 (DMN-Entscheidungstabelle 'skill_routing').
    """

    def __init__(
        self,
        registry: SkillRegistry,
        media_pipe_gateway: Any,
        dmn_engine: Any,
    ) -> None:
        self.registry = registry
        self.gateway = media_pipe_gateway
        self.dmn = dmn_engine

    @staticmethod
    def _check_missing_required_fields(
        manifest: SkillManifest, payload: dict[str, Any]
    ) -> list[str]:
        required = manifest.eingabe_schema.get("required", [])
        return [
            field
            for field in required
            if field not in payload or payload[field] in (None, "", [])
        ]

    async def detect(
        self,
        text: str,
        payload: dict[str, Any],
        allowed_skill_ids: set[str],
        schutzklasse: int,
        input_tainted: bool = True,
        explicit_skill_id: str | None = None,
    ) -> SkillDetectionResult:
        allowed_skills = self.registry.list_allowed(allowed_skill_ids, schutzklasse)
        if not allowed_skills:
            return SkillDetectionResult(
                detected_skill_id=None,
                routing_action="REJECT_NO_SKILL",
                detection_source="stage0_explicit",
                confidence=1.0,
                candidates_evaluated=[],
                dmn_rule_id="R0_NO_ALLOWED_SKILLS",
                begruendung="Für diesen Mandanten und diese Schutzklasse sind keine Skills freigeschaltet.",
            )

        allowed_map = {s.skill_id: s for s in allowed_skills}

        # 1. STUFE 0: Explizite Skill-Vorgabe (z. B. aus Fachverfahren-Request)
        if explicit_skill_id and explicit_skill_id in allowed_map:
            manifest = allowed_map[explicit_skill_id]
            missing = self._check_missing_required_fields(manifest, payload)
            if missing:
                return SkillDetectionResult(
                    detected_skill_id=explicit_skill_id,
                    routing_action="REQUIRE_CLARIFICATION",
                    detection_source="stage0_explicit",
                    confidence=1.0,
                    candidates_evaluated=[explicit_skill_id],
                    dmn_rule_id="R2_PFLICHTANGABEN_FEHLEN",
                    begruendung=f"Expliziter Skill '{explicit_skill_id}', aber Pflichtfelder fehlen: {missing}",
                    missing_fields=missing,
                )
            if manifest.wirkung == "irreversible" and input_tainted:
                return SkillDetectionResult(
                    detected_skill_id=explicit_skill_id,
                    routing_action="ROUTE_TO_WORKBENCH",
                    detection_source="stage0_explicit",
                    confidence=1.0,
                    candidates_evaluated=[explicit_skill_id],
                    dmn_rule_id="R3_IRREVERSIBLE_ODER_TAINTED_WRITE_SCHUTZ",
                    begruendung="CaMeL-Schutzregel (F-10): Irreversibler Skill auf tainted Eingabe erfordert Vorab-Freigabe.",
                )
            return SkillDetectionResult(
                detected_skill_id=explicit_skill_id,
                routing_action="AUTO_EXECUTE"
                if manifest.wirkung != "irreversible"
                else "ROUTE_TO_WORKBENCH",
                detection_source="stage0_explicit",
                confidence=1.0,
                candidates_evaluated=[explicit_skill_id],
                dmn_rule_id="R0_EXPLICIT_MATCH",
                begruendung=f"Skill '{explicit_skill_id}' deterministisch per Parameter angefordert.",
            )

        # 2. STUFE 0: Deterministische Regex-/Schlüsselwort-Trigger
        trigger_matches = self.registry.match_triggers(text, allowed_skills)
        if len(trigger_matches) == 1:
            matched_id = trigger_matches[0]
            manifest = allowed_map[matched_id]
            missing = self._check_missing_required_fields(manifest, payload)
            if not missing and not (manifest.wirkung == "irreversible" and input_tainted):
                return SkillDetectionResult(
                    detected_skill_id=matched_id,
                    routing_action="AUTO_EXECUTE",
                    detection_source="stage0_trigger",
                    confidence=0.99,
                    candidates_evaluated=trigger_matches,
                    dmn_rule_id="R0_SINGLE_TRIGGER_MATCH",
                    begruendung=f"Eindeutiger deterministischer Trigger (Stufe 0) für Skill '{matched_id}'.",
                )

        # 3. STUFE 0.5: Deterministische Top-5-Kandidaten-Vorauswahl über SQLite FTS5 (LLM-Wiki Skill-Knoten)
        if len(allowed_skills) > 5:
            candidates = self.registry.top_k_by_wiki_fts(text, allowed_skills, k=5)
            for tm in trigger_matches:
                if tm not in candidates:
                    candidates = [tm] + candidates[:4]
        else:
            candidates = [s.skill_id for s in allowed_skills]

        # 4. STUFE 1: Kalibrierter Decision-Aufruf über MediaPipe Decision Port
        candidate_descriptions = "\n".join(
            f"- {sid}: {allowed_map[sid].beschreibung}" for sid in candidates
        )
        decision_state = (
            f"Verfügbare Skills:\n{candidate_descriptions}\n\n"
            f"Eingabe-Anfrage:\n{text[:1500]}"
        )
        decision = await self.gateway.evaluate_decision_contract(
            contract_id="skill.detector",
            state_text=decision_state,
            dynamic_options={"ziel_skill": candidates},
        )

        chosen_skill_id: str = decision["questions"]["ziel_skill"]["value"]
        chosen_skill_pfad: str = decision["questions"]["ziel_skill"]["path"]
        chosen_conf: float = float(decision["questions"]["ziel_skill"]["confidence"])
        kein_skill_pfad: str = decision["questions"]["kein_skill_passt"]["path"]
        fehlen_pfad: str = decision["questions"]["pflichtangaben_fehlen"]["path"]

        manifest = allowed_map.get(chosen_skill_id, allowed_skills[0])
        missing = self._check_missing_required_fields(manifest, payload)
        if missing:
            fehlen_pfad = "yes"

        # 5. STUFE 0: Deterministische DMN-Entscheidungstabelle 'skill_routing'
        dmn_res = self.dmn.evaluate(
            table_id="skill_routing",
            inputs={
                "kein_skill_passt_pfad": kein_skill_pfad,
                "pflichtangaben_fehlen_pfad": fehlen_pfad,
                "ziel_skill_pfad": chosen_skill_pfad,
                "skill_wirkung": manifest.wirkung,
                "input_tainted": input_tainted,
            },
        )

        routing_action = dmn_res.output["routing_action"]
        return SkillDetectionResult(
            detected_skill_id=None if routing_action == "REJECT_NO_SKILL" else chosen_skill_id,
            routing_action=routing_action,
            detection_source="stage1_decision_dmn",
            confidence=chosen_conf,
            candidates_evaluated=candidates,
            dmn_rule_id=dmn_res.matched_rule_ids[0],
            begruendung=dmn_res.begruendung_text,
            missing_fields=missing,
        )
