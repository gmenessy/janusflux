"""Dauerhafter Ausführungskontext (`RunContext`, P6, LWP-07) gemäß Abschnitt 3.3 der Spezifikation."""

from typing import Any

from plattform.skills.models import SkillDetectionResult, SkillExecutionResult


class RunContext:
    """Dauerhafter Ausführungskontext eines Vorgangs mit Zwei-Phasen-Checkpointing,
    CaMeL-Taint-Tracking und integrierter Skill-Engine.
    """

    def __init__(
        self,
        run_id: str,
        tenant_id: str,
        schutzklasse: int,
        allowed_skills: set[str],
        skill_detector: Any,
        skill_executor: Any,
        audit_writer: Any,
    ) -> None:
        self.run_id = run_id
        self.tenant_id = tenant_id
        self.schutzklasse = schutzklasse
        self.allowed_skills = allowed_skills
        self._skill_detector = skill_detector
        self._skill_executor = skill_executor
        self._audit = audit_writer
        self._tainted_keys: set[str] = set()
        self.journal: list[dict[str, Any]] = []

    def mark_tainted(self, key: str) -> None:
        self._tainted_keys.add(key)

    @property
    def has_tainted_data(self) -> bool:
        return len(self._tainted_keys) > 0

    async def step(
        self,
        step_id: str,
        fn: Any,
        *,
        stufe: int,
        begruendung: str,
        **kwargs: Any,
    ) -> Any:
        """Zwei-Phasen-Checkpointing: STARTED -> Ausführung ohne DB-Lock -> COMPLETED."""
        self.journal.append({"step_id": step_id, "status": "STARTED", "stufe": stufe})
        result = await fn(self, **kwargs)
        self.journal.append({"step_id": step_id, "status": "COMPLETED", "stufe": stufe})
        await self._audit.record_step(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            step_id=step_id,
            stufe=stufe,
            schutzklasse=self.schutzklasse,
            path_taken="auto" if stufe < 3 else "human",
        )
        return result

    async def detect_skill(
        self,
        text: str,
        payload: dict[str, Any],
        *,
        explicit_skill_id: str | None = None,
        stufe: int = 1,
        begruendung: str = "Vierstufige Skill-Erkennung über Trigger, MediaPipe-Embedding, Decision-Port und DMN",
    ) -> SkillDetectionResult:
        """Erkennt den passenden Plattform-Skill für eine Anfrage oder ein Dokument."""
        return await self._skill_detector.detect(
            text=text,
            payload=payload,
            allowed_skill_ids=self.allowed_skills,
            schutzklasse=self.schutzklasse,
            input_tainted=self.has_tainted_data,
            explicit_skill_id=explicit_skill_id,
        )

    async def skill(
        self,
        skill_id: str,
        inputs: dict[str, Any],
        *,
        stufe: int = 0,
        begruendung: str,
    ) -> SkillExecutionResult:
        """Führt einen namentlich bekannten Skill mit Budget- und Taint-Prüfung aus."""
        if skill_id not in self.allowed_skills:
            raise PermissionError(
                f"Skill '{skill_id}' ist für Mandant '{self.tenant_id}' nicht freigegeben."
            )
        return await self._skill_executor.execute(
            ctx=self,
            skill_id=skill_id,
            inputs=inputs,
            input_tainted=self.has_tainted_data,
        )

    async def detect_and_run_skill(
        self,
        text: str,
        payload: dict[str, Any],
        *,
        explicit_skill_id: str | None = None,
        stufe: int = 1,
        begruendung: str = "Automatische Skill-Erkennung und kontrollierte Ausführung",
    ) -> dict[str, Any]:
        """Kombinierter End-to-End-Aufruf: Erkennt den Skill und führt ihn bei AUTO_EXECUTE direkt aus."""
        detection = await self.detect_skill(
            text=text,
            payload=payload,
            explicit_skill_id=explicit_skill_id,
            stufe=stufe,
            begruendung=begruendung,
        )
        if detection.routing_action == "AUTO_EXECUTE" and detection.detected_skill_id:
            execution = await self.skill(
                skill_id=detection.detected_skill_id,
                inputs=payload,
                stufe=0,
                begruendung=f"Ausführung des erkannten Skills '{detection.detected_skill_id}'",
            )
            return {"detection": detection.model_dump(), "execution": execution.model_dump()}

        return {"detection": detection.model_dump(), "execution": None}
