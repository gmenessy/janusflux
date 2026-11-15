"""Datenmodelle für Skill-Manifeste, Skill-Detektion und Skill-Ausführung (LWP-05)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillStepSpec(BaseModel):
    step_id: str
    stufe: Literal[0, 1, 2, 3]
    action: str
    contract: str | None = None
    table_id: str | None = None
    prompt_id: str | None = None
    level: Literal["L1", "L2", "L3"] | None = None
    begruendung: str = Field(min_length=10)


class SkillBudgetSpec(BaseModel):
    llm_aufrufe: int = Field(ge=0, le=3)
    max_input_tokens: int = Field(ge=128, le=4096)
    zeit_s: int = Field(ge=5, le=120)


class SkillManifest(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    version: str
    name: str
    art: Literal["atomar", "zusammengesetzt"]
    beschreibung: str
    beispiele: list[str] = Field(default_factory=list)
    trigger_patterns: list[str] = Field(default_factory=list)
    stufen: list[Literal[0, 1, 2, 3]]
    wirkung: Literal["read", "reversible_write", "irreversible"]
    max_schutzklasse: Literal[0, 1, 2, 3] = 3
    eingabe_schema: dict[str, Any]
    ausgabe_schema: dict[str, Any]
    steps: list[SkillStepSpec]
    budget: SkillBudgetSpec
    verantwortlich: dict[str, str]


class SkillDetectionResult(BaseModel):
    detected_skill_id: str | None
    routing_action: Literal[
        "AUTO_EXECUTE", "REQUIRE_CLARIFICATION", "ROUTE_TO_WORKBENCH", "REJECT_NO_SKILL"
    ]
    detection_source: Literal["stage0_explicit", "stage0_trigger", "stage1_decision_dmn"]
    confidence: float = Field(ge=0.0, le=1.0)
    candidates_evaluated: list[str]
    dmn_rule_id: str
    begruendung: str
    missing_fields: list[str] = Field(default_factory=list)


class SkillExecutionResult(BaseModel):
    skill_id: str
    version: str
    status: Literal["COMPLETED", "WAITING_HUMAN", "CLARIFICATION_NEEDED", "FAILED_SAFE"]
    output: dict[str, Any]
    steps_executed: list[str]
    llm_calls_used: int
    latency_ms: int
