---
node_id: "skill:eingang.routen"
node_type: "skill"
skill_id: "eingang.routen"
titel: "Posteingangs-Klassifikation & Zuständigkeits-Routing"
schutzklasse: 0
dmn_table_id: "eingang_routing"
manifest_path: "skills/eingang.routen/skill.yaml"
trigger_keywords:
  - "Posteingang"
  - "Zuständigkeits-Routing"
  - "Poststelle zuordnen"
  - "Fachamt"
links: []
---
# Skill: eingang.routen

Klassifiziert eingehende Bürgeranliegen, E-Mails und Schreiben über den kalibrierten MediaPipe Decision Port (Stufe 1) und routet sie deterministisch über die DMN-Entscheidungstabelle `eingang_routing` an das zuständige Fachamt.
