---
node_id: "skill:auskunft.recht"
node_type: "skill"
skill_id: "auskunft.recht"
titel: "Quellengebundene Rechts- und Satzungsauskunft"
schutzklasse: 0
dmn_table_id: "skill_routing"
manifest_path: "skills/auskunft.recht/skill.yaml"
trigger_keywords:
  - "Rechtsauskunft"
  - "Satzungsauskunft"
  - "Rechtsgrundlage"
  - "Sondernutzungssatzung"
links:
  - "§ 31 VwVfG"
  - "§ 4 Sondernutzungssatzung"
---
# Skill: auskunft.recht

Beantwortet rechtliche und fachliche Fragen zu Gesetzen ([[§ 31 VwVfG]]) und kommunalen Satzungen ([[§ 4 Sondernutzungssatzung]]) direkt über das strukturierte LLM-Wiki (SQLite FTS5 / BM25 + nativer 1-Hop-Verweisgraph) mit strikter Zitatprüfung (`VAL-007`).
