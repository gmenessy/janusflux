"""SkillExecutor mit CaMeL-Taint-Tracking, Budget-Durchsetzung und Zwei-Phasen-Checkpointing (LWP-07)."""

import time
from typing import Any

from plattform.skills.models import SkillExecutionResult, SkillManifest
from plattform.skills.registry import SkillRegistry


class SkillSecurityViolationError(PermissionError):
    """Wird bei Verletzung von CaMeL-Taint-Regeln oder Budgetgrenzen ausgelöst."""


class SkillExecutor:
    """Führt einen erkannten Skill Schritt für Schritt über den RunContext aus,
    erzwingt Taint-Tracking (F-10), Token-/Aufruf-Budgets und Zwei-Phasen-Checkpointing.
    """

    def __init__(self, registry: SkillRegistry, action_handlers: dict[str, Any]) -> None:
        self.registry = registry
        self.handlers = action_handlers

    async def execute(
        self,
        ctx: Any,
        skill_id: str,
        inputs: dict[str, Any],
        input_tainted: bool = True,
    ) -> SkillExecutionResult:
        start_ns = time.perf_counter_ns()
        manifest: SkillManifest = self.registry.get(skill_id)

        # 1. CaMeL-Taint-Prüfung (F-10): Irreversible Wirkung auf tainted Daten erfordert Stufe-3-Schritt
        if input_tainted and manifest.wirkung == "irreversible" and not any(
            s.stufe == 3 for s in manifest.steps
        ):
            raise SkillSecurityViolationError(
                f"CaMeL-Verstoß: Skill '{skill_id}' ist irreversibel und Eingabe ist tainted."
            )

        # 2. Pflichtfeld-Prüfung vor Ausführung
        required_fields = manifest.eingabe_schema.get("required", [])
        missing = [f for f in required_fields if f not in inputs or inputs[f] is None]
        if missing:
            elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
            return SkillExecutionResult(
                skill_id=skill_id,
                version=manifest.version,
                status="CLARIFICATION_NEEDED",
                output={"missing_fields": missing},
                steps_executed=[],
                llm_calls_used=0,
                latency_ms=elapsed_ms,
            )

        state: dict[str, Any] = dict(inputs)
        steps_executed: list[str] = []
        llm_calls_used = 0

        # 3. Sequenzielle Ausführung der deklarierten Skill-Schritte
        for step in manifest.steps:
            if step.stufe == 2:
                llm_calls_used += 1
                if llm_calls_used > manifest.budget.llm_aufrufe:
                    raise SkillSecurityViolationError(
                        f"Budget-Überschreitung in Skill '{skill_id}': "
                        f"Maximal {manifest.budget.llm_aufrufe} Stufe-2-Aufrufe erlaubt."
                    )

            handler = self.handlers.get(step.action)
            if handler is None:
                raise KeyError(
                    f"Kein Action-Handler für '{step.action}' in Skill '{skill_id}' registriert."
                )

            # Ausführung über den RunContext für Zwei-Phasen-Checkpointing & Audit-Hash-Kette
            step_output = await ctx.step(
                step_id=f"{skill_id}.{step.step_id}",
                fn=handler,
                state=state,
                step_spec=step.model_dump(),
                stufe=step.stufe,
                begruendung=step.begruendung,
            )

            steps_executed.append(step.step_id)
            if isinstance(step_output, dict):
                state.update(step_output)
                if step_output.get("__status__") == "WAITING_HUMAN":
                    elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
                    return SkillExecutionResult(
                        skill_id=skill_id,
                        version=manifest.version,
                        status="WAITING_HUMAN",
                        output=state,
                        steps_executed=steps_executed,
                        llm_calls_used=llm_calls_used,
                        latency_ms=elapsed_ms,
                    )

        elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        return SkillExecutionResult(
            skill_id=skill_id,
            version=manifest.version,
            status="COMPLETED",
            output=state,
            steps_executed=steps_executed,
            llm_calls_used=llm_calls_used,
            latency_ms=elapsed_ms,
        )
