---
node_id: "skill:dokument.extrahieren"
node_type: "skill"
skill_id: "dokument.extrahieren"
titel: "Strukturierte Feld-Extraktion mit Fundstellennachweis"
schutzklasse: 0
dmn_table_id: "skill_routing"
manifest_path: "skills/dokument.extrahieren/skill.yaml"
trigger_keywords:
  - "extrahieren"
  - "extrahiere"
  - "Felder auslesen"
  - "IBAN"
  - "Kaltmiete"
links: []
---
# Skill: dokument.extrahieren

Extrahiert strukturierte Datenfelder (Beträge, IBAN, Steuer-ID, Zeiträume, Kaltmiete) aus Bescheiden, Rechnungen oder Anträgen mit wörtlichem Zitat und deterministischer `VAL-007`-Abwärtsprüfung.
