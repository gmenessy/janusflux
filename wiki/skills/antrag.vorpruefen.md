---
node_id: "skill:antrag.vorpruefen"
node_type: "skill"
skill_id: "antrag.vorpruefen"
titel: "Formale Antrags-Vorprüfung & Vollständigkeitscheck"
schutzklasse: 0
dmn_table_id: "antrag_vollstaendigkeit"
manifest_path: "skills/antrag.vorpruefen/skill.yaml"
trigger_keywords:
  - "Vollständigkeitsprüfung"
  - "Wohngeldantrag"
  - "Vorprüfung"
  - "Einkommensnachweis"
  - "Mietbescheinigung"
links:
  - "§ 31 VwVfG"
---
# Skill: antrag.vorpruefen

Prüft Anträge (Wohngeld, Elterngeld, Sondernutzung) auf Pflichtfelder und vorhandene Nachweise, berechnet Nachforderungsfristen nach [[§ 31 VwVfG]] und ordnet den Vorgang deterministisch über `antrag_vollstaendigkeit` in Korb GRÜN, GELB oder ROT ein.
