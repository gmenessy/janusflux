"""Deterministische DMN-Entscheidungstabellen-Engine (LWP-03, Stufe 0).
Unterstützt Hit-Policies:
- UNIQUE (inkl. statischer Disjunktheits-Prüfung beim Laden und zur Laufzeit)
- FIRST
- COLLECT_LIST
- COLLECT_SUM
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class DMNOverlapError(ValueError):
    """Wird ausgelöst, wenn eine UNIQUE-Tabelle überlappende Regeln enthält."""


class DMNCondition(BaseModel):
    field: str
    operator: Literal["==", "!=", "<", "<=", ">", ">=", "in"]
    value: Any


class DMNRule(BaseModel):
    rule_id: str
    conditions: list[DMNCondition] = Field(default_factory=list)
    output: dict[str, Any]
    begruendung_template: str


class DMNTableSpec(BaseModel):
    table_id: str
    version: int = 1
    hit_policy: Literal["UNIQUE", "FIRST", "COLLECT_LIST", "COLLECT_SUM"]
    owner: str
    rechtsgrundlage: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    rules: list[DMNRule]


@dataclass(frozen=True)
class DMNEvaluationResult:
    table_id: str
    version: int
    hit_policy: str
    matched_rule_ids: list[str]
    output: dict[str, Any]
    begruendung_text: str


class DMNEngine:
    """Lädt, validiert und wertet deterministische DMN-Entscheidungstabellen aus."""

    def __init__(self, rules_dir: Path = Path("rules")) -> None:
        self.rules_dir = rules_dir
        self._tables: dict[str, DMNTableSpec] = {}
        if self.rules_dir.exists():
            self.load_all()

    def load_all(self) -> None:
        if not self.rules_dir.exists():
            return
        for path in sorted(self.rules_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.register_table(DMNTableSpec.model_validate(raw))

    def register_table(self, spec: DMNTableSpec) -> None:
        if spec.hit_policy == "UNIQUE":
            self._verify_unique_disjointness(spec)
        self._tables[spec.table_id] = spec

    @staticmethod
    def _conditions_can_overlap(r1: DMNRule, r2: DMNRule, all_fields: list[str]) -> bool:
        """Prüft statisch, ob zwei Regeln gleichzeitig feuern können."""
        c1_map: dict[str, list[DMNCondition]] = {}
        for c in r1.conditions:
            c1_map.setdefault(c.field, []).append(c)
        c2_map: dict[str, list[DMNCondition]] = {}
        for c in r2.conditions:
            c2_map.setdefault(c.field, []).append(c)

        for field in all_fields:
            conds1 = c1_map.get(field, [])
            conds2 = c2_map.get(field, [])
            if not conds1 or not conds2:
                continue
            if DMNEngine._field_constraints_disjoint(conds1, conds2):
                return False
        return True

    @staticmethod
    def _field_constraints_disjoint(
        conds1: list[DMNCondition], conds2: list[DMNCondition]
    ) -> bool:
        for c1 in conds1:
            for c2 in conds2:
                if c1.operator == "==" and c2.operator == "==" and c1.value != c2.value:
                    return True
                if c1.operator == "==" and c2.operator == "!=" and c1.value == c2.value:
                    return True
                if c1.operator == "!=" and c2.operator == "==" and c1.value == c2.value:
                    return True
                if (
                    c1.operator in ("<", "<=")
                    and c2.operator in (">", ">=")
                    and isinstance(c1.value, (int, float))
                    and isinstance(c2.value, (int, float))
                ) and (c1.value < c2.value or (
                    c1.value == c2.value and (c1.operator == "<" or c2.operator == ">")
                )):
                    return True
                if (
                    c2.operator in ("<", "<=")
                    and c1.operator in (">", ">=")
                    and isinstance(c1.value, (int, float))
                    and isinstance(c2.value, (int, float))
                ) and (c2.value < c1.value or (
                    c2.value == c1.value and (c2.operator == "<" or c1.operator == ">")
                )):
                    return True
        return False

    @classmethod
    def _verify_unique_disjointness(cls, spec: DMNTableSpec) -> None:
        fields = list(spec.input_schema.keys())
        for i in range(len(spec.rules)):
            for j in range(i + 1, len(spec.rules)):
                r1 = spec.rules[i]
                r2 = spec.rules[j]
                if cls._conditions_can_overlap(r1, r2, fields):
                    raise DMNOverlapError(
                        f"DMN UNIQUE-Tabelle '{spec.table_id}' hat überlappende Regeln: "
                        f"'{r1.rule_id}' und '{r2.rule_id}'."
                    )

    @staticmethod
    def _eval_condition(cond: DMNCondition, inputs: dict[str, Any]) -> bool:
        if cond.field not in inputs:
            return False
        actual = inputs[cond.field]
        expected = cond.value
        if cond.operator == "==":
            return bool(actual == expected)
        if cond.operator == "!=":
            return bool(actual != expected)
        if cond.operator == "<":
            return bool(actual < expected)
        if cond.operator == "<=":
            return bool(actual <= expected)
        if cond.operator == ">":
            return bool(actual > expected)
        if cond.operator == ">=":
            return bool(actual >= expected)
        if cond.operator == "in":
            return bool(actual in expected)
        return False

    def evaluate(self, table_id: str, inputs: dict[str, Any]) -> DMNEvaluationResult:
        if table_id not in self._tables:
            raise KeyError(f"Unbekannte DMN-Tabelle: '{table_id}'")
        spec = self._tables[table_id]

        matched: list[DMNRule] = []
        for rule in spec.rules:
            if all(self._eval_condition(c, inputs) for c in rule.conditions):
                matched.append(rule)
                if spec.hit_policy == "FIRST":
                    break

        if not matched:
            raise ValueError(
                f"DMN-Tabelle '{table_id}' lieferte keinen Regeltreffer für Eingabe: {inputs}"
            )

        if spec.hit_policy == "UNIQUE" and len(matched) > 1:
            raise DMNOverlapError(
                f"DMN UNIQUE-Verletzung zur Laufzeit in '{table_id}': "
                f"{[r.rule_id for r in matched]}"
            )

        if spec.hit_policy in ("UNIQUE", "FIRST"):
            winner = matched[0]
            return DMNEvaluationResult(
                table_id=spec.table_id,
                version=spec.version,
                hit_policy=spec.hit_policy,
                matched_rule_ids=[winner.rule_id],
                output=dict(winner.output),
                begruendung_text=winner.begruendung_template,
            )

        if spec.hit_policy == "COLLECT_LIST":
            return DMNEvaluationResult(
                table_id=spec.table_id,
                version=spec.version,
                hit_policy=spec.hit_policy,
                matched_rule_ids=[r.rule_id for r in matched],
                output={"items": [dict(r.output) for r in matched]},
                begruendung_text=" | ".join(r.begruendung_template for r in matched),
            )

        # COLLECT_SUM
        summed: dict[str, Any] = {}
        for r in matched:
            for k, v in r.output.items():
                if isinstance(v, (int, float)):
                    summed[k] = summed.get(k, 0) + v
                else:
                    summed[k] = v
        return DMNEvaluationResult(
            table_id=spec.table_id,
            version=spec.version,
            hit_policy=spec.hit_policy,
            matched_rule_ids=[r.rule_id for r in matched],
            output=summed,
            begruendung_text=" + ".join(r.begruendung_template for r in matched),
        )
