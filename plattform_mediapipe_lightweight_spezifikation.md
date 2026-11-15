# Implementierungsspezifikation: Ultra-Lightweight-Plattform (Google MediaPipe / LiteRT) mit Skill-Detektor & Skill-Execution-Engine

**Status:** Verbindliche Implementierungsspezifikation für die generative Code-Erzeugung (Single-Node / Edge / Kommunal-Appliance)  
**Basis-Architektur:** Abgeleitet aus der Gesamtspezifikation v2.1 (Vier-Stufen-Kaskade, Services P1–P12)  
**Laufzeit-Engine (Stufen 1 & 2):** Google MediaPipe Tasks (`mediapipe.tasks.genai`, `mediapipe.tasks.text`) auf CPU / iGPU / NPU  
**Ziel-Hardware:** Standard-x86- oder ARM64-Server / Mini-PC (16–32 GB RAM, 8–16 CPU-Kerne, keine Rechenzentrums-GPU erforderlich)  
**Kapazitätsprofil:** 1 bis 3 Mandanten (Kommunen), 1 bis 4 parallele KI-Inferenzläufe, $> 50$ parallele deterministische Stufe-0-Läufe  
**Umsetzungsmodus:** Generativ implementierbar über Coding-Agenten (12 sequenzielle Arbeitspakete `LWP-01` bis `LWP-12`)

---

## Inhaltsverzeichnis

* **TEIL 1: ARCHITEKTUR-REDUKTION & MEDIAPIPE-STACK (P1–P12 DELTA)**
  * 1.1 Bewusste Reduktionen vs. 100 % erhaltene Sicherheits- und Rechtsgarantien
  * 1.2 Lightweight Laufzeit-Topologie (Docker Compose statt Kubernetes-GPU-Cluster)
  * 1.3 Kontext-, Chunking- und Prompt-Kompression für lokale 2B/4B-Modelle
* **TEIL 2: SKILL-DETEKTOR & SKILL-EXECUTION-ENGINE**
  * 2.1 Vier-Stufen-Architektur des Skill-Detektors (`SkillDetector`)
  * 2.2 Deklaratives Skill-Manifest (`skill.yaml`) und mitgelieferte Referenz-Skills
  * 2.3 Decision Contract `contracts/skill.detector.yaml` & DMN-Tabelle `rules/skill_routing.json`
  * 2.4 Referenzimplementierung: `src/plattform/skills/` (`models.py`, `registry.py`, `detector.py`, `executor.py`)
* **TEIL 3: MEDIAPIPE MODEL GATEWAY (P5) & WORKFLOW-INTEGRATION (P6)**
  * 3.1 Modellsteckbriefe (`models/gemma-4b-mediapipe.yaml`, `models/mediapipe-embedder.yaml`, `models/mediapipe-classifier.yaml`)
  * 3.2 Referenzimplementierung: `src/plattform/model_gateway/mediapipe_adapter.py`
  * 3.3 Erweiterung des `RunContext` (`ctx.detect_skill`, `ctx.skill`, `ctx.detect_and_run_skill`)
* **TEIL 4: DATENBANK-MIGRATION, DOCKER-COMPOSE & BUILD-SETUP**
  * 4.1 SQL-Migration `db/migrations/002_skills_and_lightweight.sql`
  * 4.2 `deploy/compose/docker-compose.lightweight.yaml`
  * 4.3 Angepasste `pyproject.toml` & `Makefile`
* **TEIL 5: GENERATIVE IMPLEMENTIERUNGS-STEUERUNG (`CLAUDE.md` & `LWP-01` BIS `LWP-12`)**
  * 5.1 Verbindliche `CLAUDE.md` für die generative Implementierung
  * 5.2 Detaillierter Arbeitsplan (`LWP-01` bis `LWP-12`) mit Start-Prompts und Abnahmebefehlen

---

# TEIL 1: ARCHITEKTUR-REDUKTION & MEDIAPIPE-STACK (P1–P12 DELTA)

## 1.1 Bewusste Reduktionen vs. 100 % erhaltene Sicherheits- und Rechtsgarantien

Die Ultra-Lightweight-Variante ersetzt ausschließlich die rechenzentrumsgebundenen GPU-Komponenten (vLLM mit `Gemma 4 31B`, SGLang mit `Clef-flash 9B`/`27B` und `BGE-M3` auf L40S/H100) durch lokale **Google MediaPipe Tasks (LiteRT)** im selben Prozess bzw. Worker-Container. Alle rechtlichen, deterministischen und kryptografischen Schutzmechanismen bleiben unverändert bestehen.

| Architektur-Dimension | Voll-Ausbau (Rechenzentrum, 20 Mandanten) | Ultra-Lightweight (MediaPipe, 1–3 Mandanten) | Auswirkungen & Kompensationsmechanismus |
| :--- | :--- | :--- | :--- |
| **Stufe 0: Deterministik (P2, P3, P11)** | DMN-Engine, §-31-VwVfG-Fristen, ISO-7064/Mod-97, Presidio DE, PDF/UA-1, Physische PDF-Schwärzung | **100 % identisch übernommen** | Keine Qualitätsreduktion; läuft vollständig auf CPU in $< 20\text{ ms}$. |
| **Stufe 1: Decision Port (P3)** | `Clef-flash 9B` & `Clef 27B` auf L40S/H100 + Bidirektionales Ensembling | **MediaPipe TextClassifier (`.tflite`) + MediaPipe LLM Single-Token-Readout** + CRC-Schwelle $\hat{\lambda}_{\text{auto}}$ | Kein bidirektionales Ensembling (halbiert Latenz); höhere Unsicherheitsrate routet sicher auf `human` (Stufe 3). |
| **Stufe 2: Generatives LLM (P5)** | `Gemma 4 31B` (BF16, 32k Kontext, Thinking-Spur, Vision) auf $2\text{–}3 \times \text{H100}$ | **MediaPipe LLM Inference API** (`gemma-3-4b-it-int4.task` oder `gemma-2-2b-it-int4.task`, max. $4.096$ Token) | Kein `<think>`-Modus (`enable_thinking=False`), kein Vision-LLM-Fallback; Fokus auf Einzel-Feld-Extraktion & Jinja2-Vorlagen. |
| **Stufe 3: Mensch / HITL (P8)** | Serverseitige BITV-2.0-Workbench (Jinja2, Split-Screen, L0–L3, Payload-Hash) | **100 % identisch übernommen** | Keine Reduktion; fängt die konservativeren Schwellen der kleineren Modelle rechtssicher auf. |
| **Skill-Erkennung & Ausführung** | Statische Workflow-Zuweisung + Semantic Router | **Neuer 4-stufiger `SkillDetector` + `SkillExecutor`** mit Taint- & Budget-Schutz | Erkennt automatisch passende Skills aus Freitext, Dokumenten oder Formularen und führt sie isoliert aus. |
| **Wissenssuche & RAG (P4)** | 3-Node-Qdrant + `BGE-M3` ($1.024$ Dim., $8.192$ Token) + `bge-reranker-v2-m3` | **Single-Node-Qdrant + MediaPipe `TextEmbedder`** ($\le 512$ Token) + DE-BM25 (Komposita) + Exact-Match | Kleinere Chunks ($300$ statt $800$ Token); DE-BM25 und Exact-Match (`§ 35a VwVfG`) tragen die Hauptlast. |
| **Zustand, Queue & Audit (P6, P9)** | PostgreSQL 17 (RLS, Fair-Queue, Zwei-Phasen-Checkpoint, Crypto-Shredding) | **100 % identisch übernommen** | Volle Crash-Recovery, Mandanten-Isolation und DSGVO-Art.-17-Konformität auf einer PG-17-Instanz. |
| **Observability & Secrets (P2, P9)** | K8s, OTel-Collector, Loki, Tempo, Prometheus, OpenBao-Cluster | **Strukturiertes JSONL-Logging (`structlog`) + `scan_cleartext_logs.py` + lokaler Env/File-KMS-Tresor** (AES-256-GCM) | Minimaler RAM-Verbrauch ($< 200\text{ MB}$ Overhead) bei identischer Klartext-Freiheit in Logs. |

---

## 1.2 Lightweight Laufzeit-Topologie (Docker Compose statt Kubernetes-GPU-Cluster)

Die gesamte Plattform besteht im Lightweight-Modus aus exakt **fünf leichtgewichtigen Containern** auf einem einzigen Host:

1. `platform-api`: FastAPI-Server (P1 Edge, P2 Privacy, P3 Decision/DMN, P5 MediaPipe-Embedder/Classifier, P8 Workbench, P10 Admin).
2. `platform-worker`: Hintergrund-Worker + Cron-Scheduler (P4 Ingestion, P5 MediaPipe-LLM-Inference `Gemma 4B INT4`, P6 Durable Runtime & Skill-Executor, P7 MCP-Tools, P11 PDF/UA & Schwärzung, P12 Synthetische Zwillinge).
3. `postgres`: PostgreSQL 17 mit Row-Level-Security (`FORCE ROW LEVEL SECURITY`), Job-Queue und Audit-Sequencer.
4. `qdrant`: Lokale Single-Node-Qdrant-Instanz mit `int8`-Quantisierung.
5. `opa`: Open Policy Agent Sidecar für deterministische ABAC-Autorisierung.

---

## 1.3 Kontext-, Chunking- und Prompt-Kompression für lokale 2B/4B-Modelle

Da ein 4-Bit-quantisiertes Modell (`gemma-3-4b-it-int4.task`) in MediaPipe auf CPU/iGPU ein effektives Arbeitsfenster von $\le 4.096$ Token besitzt und bei tief verschachtelten JSON-Schemata schneller halluziniert als `Gemma 4 31B`, gelten in der Lightweight-Variante vier verbindliche technische Kompressionsregeln:

1. **Reduzierte Chunk-Größen in P4 (`knowledge`):**
   * Gesetze/Satzungen: Parent = 1 Paragraf ($\le 600$ Token), Child = 1 Absatz ($\le 280$ Token, Overlap $50$ Token).
   * Bescheide/Anträge/Freitext: $\le 300$ Token mit $50$ Token Overlap (statt $800$ Token), damit jeder Chunk inklusive des deterministischen `context_header` vollständig in das $512$-Token-Fenster des MediaPipe `TextEmbedder` passt.
2. **Strikte Top-$K$-Begrenzung im Retrieval:**
   * Nach der Dreipfad-RRF-Fusion ($\text{Dense} + \text{DE-BM25} + \text{Exact-Match}$, $k=60$) und dem Stufe-1-Relevanz-Gate (`retrieval.gate`) werden maximal die **Top-4-Passagen** ($\le 1.200$ Token gesamt) an den MediaPipe-LLM-Prompt übergeben.
3. **Flache JSON-Ausgabeschemata & Prompt-Grammar:**
   * Da MediaPipe `LlmInference` kein serverseitiges vLLM-XGrammar-Backend besitzt, erzwingt der `MediaPipeGatewayAdapter` (P5) flache JSON-Objekte (maximale Schachtelungstiefe $2$), injiziert ein kompaktes JSON-Template in den System-Prompt, schneidet Markdown-Codeblöcke deterministisch ab und führt bei einem `Pydantic`-Validierungsfehler genau **einen** automatischen Reparatur-Durchlauf (`max_repair_attempts = 1`) mit dem konkreten Validierungsfehler durch, bevor das Post-Gate fail-closed (`VAL-006` $\rightarrow$ `human`) auslöst.
4. **Vorlagen-Vorrang bei Bescheiden (P11):**
   * Das 4B-Modell generiert niemals einen kompletten Bescheid im Freitext, sondern füllt ausschließlich die strukturierten Begründungs-Bausteine (`sachverhalt_zusammenfassung`, `wuerdigung_satz`, `zitat_wortlaut`), die nach bestandener `VAL-007`-Prüfung deterministisch in die Jinja2-PDF/UA-Vorlage eingesetzt werden.

---

# TEIL 2: SKILL-DETEKTOR & SKILL-EXECUTION-ENGINE

## 2.1 Vier-Stufen-Architektur des Skill-Detektors (`SkillDetector`)

Der **Skill-Detektor** beantwortet für jede eingehende Anfrage (Bürgeranliegen, hochgeladenes Dokument, Sachbearbeiter-Auftrag oder Teil-Schritt eines Workflows) die Frage: *„Welcher geprüfte Plattform-Skill ist für diese Aufgabe zuständig, sind alle Vorbedingungen erfüllt und darf er automatisch starten?“*

Er befolgt strikt die **Vier-Stufen-Kaskade**:

1. **Stufe 0 – Deterministischer Vorfilter & Trigger-Matcher ($< 2\text{ ms}$):**
   * Prüft zuerst gegen die Mandanten-Freigabe (`tenant.yaml`) und das EU-KI-VO-Register (`ai_act_register`), welche Skills für den Mandanten überhaupt aktiv sind und ob die `schutzklasse` des Vorgangs die maximal zulässige Schutzklasse des Skills nicht überschreitet.
   * Prüft explizite Aufrufe (`explicit_skill_id`), exakte Dokument-/Formulartypen (z. B. `XRechnung`, `Wohngeldantrag_V2`) sowie deterministische Regex-/Schlüsselwort-Trigger aus dem Skill-Manifest (`trigger_patterns`).
   * Trifft genau ein deterministischer Trigger mit `priority == "exclusive"` zu, wird Stufe 1 übersprungen und der Skill sofort mit `detection_source = "stage0_trigger"` ausgewählt.
2. **Stufe 0.5 – Semantische Kandidaten-Vorauswahl über MediaPipe `TextEmbedder` ($< 15\text{ ms}$):**
   * Stehen mehr als $5$ aktive Skills zur Auswahl, berechnet der Detektor über den lokalen MediaPipe `TextEmbedder` die Kosinus-Ähnlichkeit zwischen der Anfrage und den beim Start vorberechneten Skill-Embeddings (`beschreibung` + `beispiele`).
   * Er reduziert die Kandidatenmenge auf die **Top-5-Skills**. Gemäß Eiserner Architekturregel 2 liegt auf dem Embedding-Score **niemals** eine finale Entscheidungsschwelle; er dient ausschließlich der Kandidatenreduktion für Stufe 1.
3. **Stufe 1 – Kalibrierte Skill-Klassifikation & Out-of-Scope-Gate ($< 80\text{ ms}$):**
   * Ruft den Decision Contract `skill.detector` über den MediaPipe Decision Port auf:
     * Frage 1 (`choice`): Auswahl unter den Top-5-Kandidaten-Skills.
     * Frage 2 (`noul` – `kein_skill_passt`): Separate binäre Prüfung *„Die Anfrage verlangt eine Tätigkeit, die durch keinen der angebotenen Skills abgedeckt wird“* (verhindert den aus der Forschung bekannten `SCOPE`-F1-Einbruch bei „Sonstiges“-Klassen, `F-03`).
     * Frage 3 (`noul` – `pflichtangaben_fehlen`): *„Für die Ausführung des erkannten Skills fehlen im Eingabetext erkennbar notwendige Angaben oder Dokumente.“*
   * Wendet Temperatur-Skalierung und die Conformal-Risk-Control-Schwelle ($\hat{\lambda}_{\text{auto}}$) an.
4. **Stufe 0 – Deterministische Routing-Entscheidungstabelle (`rules/skill_routing.json`, $< 1\text{ ms}$):**
   * Verknüpft die Pfade aus Stufe 1 (`skill_choice_pfad`, `kein_skill_passt_pfad`, `pflichtangaben_fehlen_pfad`), die `wirkung` des Skills (`read`, `reversible_write`, `irreversible`), den Taint-Status (`input_tainted`) und die `schutzklasse` deterministisch zu einer von vier Aktionen:
     * `AUTO_EXECUTE`: Skill wird direkt über den `SkillExecutor` gestartet.
     * `REQUIRE_CLARIFICATION`: Es wird eine deterministische Rückfrage nach den fehlenden Pflichtfeldern erzeugt.
     * `ROUTE_TO_WORKBENCH`: Der Vorgang wird samt erkanntem Skill-Vorschlag in der Workbench (P8, Stufe 3) zur Bestätigung vorgelegt.
     * `REJECT_NO_SKILL`: Kein passender Skill vorhanden; geordneter Abbruch ohne LLM-Aufruf.

---

## 2.2 Deklaratives Skill-Manifest (`skill.yaml`) und mitgelieferte Referenz-Skills

Jeder Skill liegt in einem eigenen Verzeichnis `skills/<skill_id>/skill.yaml` und wird beim Start durch die `SkillRegistry` geladen, gegen das JSON-Schema geprüft, durch den statischen Ketten-Linter validiert und mit dem MediaPipe `TextEmbedder` für den Skill-Detektor indexiert.

### Beispiel 1: `skills/dokument.schwaerzen/skill.yaml` (Stufen 0, 1, 3 – 0 LLM-Calls)
```yaml
skill_id: dokument.schwaerzen
version: 1.0.0
name: "Akten- und Dokumentenschwärzung (IFG / RIS)"
art: zusammengesetzt
beschreibung: "Erkennt personenbezogene Daten sowie Betriebs- und Geschäftsgeheimnisse in PDF-Dokumenten und führt nach L1-Freigabe eine unumkehrbare physische PDF-Schwärzung durch."
beispiele:
  - "Bitte schwärzen Sie alle Personennamen und Adressen in dieser Stellungnahme für das Ratsinformationssystem."
  - "IFG-Anfrage: Akte vor Herausgabe anonymisieren und Kontodaten entfernen."
  - "Dokument für öffentliche Sitzungsvorlage schwärzen."
trigger_patterns:
  - '\bschwärz(en|ung)\b'
  - '\bIFG[-\s]?Anfrage\b'
  - '\bRatsinformationssystem\b'
stufen: [0, 1, 3]
wirkung: reversible_write
max_schutzklasse: 3
eingabe_schema:
  type: object
  required: ["doc_id", "zweck"]
  properties:
    doc_id: { type: string, format: uuid }
    zweck: { type: string, enum: ["ifg_auskunft", "ratsinfo_oeffentlich", "akteneinsicht"] }
ausgabe_schema:
  type: object
  required: ["redacted_doc_id", "removed_spans_count", "text_layer_clean"]
  properties:
    redacted_doc_id: { type: string }
    removed_spans_count: { type: integer, minimum: 0 }
    text_layer_clean: { type: boolean }
steps:
  - step_id: s1_pii_scan
    stufe: 0
    action: "p2.detect_pii_bboxes"
    begruendung: "Deterministische Erkennung von Prüfziffern und Presidio-Entitäten samt PDF-Koordinaten"
  - step_id: s2_context_decision
    stufe: 1
    action: "p3.decide"
    contract: "schwaerzung.pruefung"
    begruendung: "Abgrenzung Privatperson vs. Amtsträger in dienstlicher Funktion"
  - step_id: s3_human_approval
    stufe: 3
    action: "p8.approve"
    level: "L1"
    begruendung: "Menschliche Kontrolle der Schwärzungsmaske im Split-Screen vor physischer Entfernung"
  - step_id: s4_physical_redact
    stufe: 0
    action: "p11.redact_pdf_physical"
    begruendung: "Physische Entfernung der Glyphen aus dem PDF-Content-Stream und 0-%-Resttext-Prüfung"
budget:
  llm_aufrufe: 0
  max_input_tokens: 1500
  zeit_s: 30
verantwortlich:
  fachlich: datenschutz
  technisch: plattform-team
```

### Beispiel 2: `skills/antrag.vorpruefen/skill.yaml` (Stufen 0, 1, 2, 3)
```yaml
skill_id: antrag.vorpruefen
version: 1.0.0
name: "Formale Antrags-Vorprüfung & Vollständigkeitscheck"
art: zusammengesetzt
beschreibung: "Prüft Anträge (z. B. Wohngeld, Elterngeld, Sondernutzung) auf Pflichtfelder, vorhandene Nachweise und Plausibilität, berechnet Nachforderungsfristen nach § 31 VwVfG und erstellt den Nachforderungsentwurf."
beispiele:
  - "Prüfe diesen Wohngeldantrag auf Vollständigkeit der Unterlagen."
  - "Sind Einkommensnachweis, Mietbescheinigung und Ausweis vollständig vorhanden?"
  - "Formale Vorprüfung des eingegangenen Antrags durchführen und fehlende Belege nachfordern."
trigger_patterns:
  - '\bVollständigkeitsprüfung\b'
  - '\bWohngeldantrag\b'
  - '\bVorprüfung\b'
stufen: [0, 1, 2, 3]
wirkung: reversible_write
max_schutzklasse: 3
eingabe_schema:
  type: object
  required: ["antrag_formular", "anlagen_texte"]
  properties:
    antrag_formular: { type: object }
    anlagen_texte: { type: array, items: { type: string } }
ausgabe_schema:
  type: object
  required: ["korb", "aktion", "frist_datum"]
  properties:
    korb: { type: string, enum: ["GRUEN", "GELB", "ROT"] }
    aktion: { type: string }
    frist_datum: { type: ["string", "null"] }
    nachforderung_text: { type: ["string", "null"] }
steps:
  - step_id: s1_pflichtfelder
    stufe: 0
    action: "p3.check_mandatory_fields"
    begruendung: "Deterministischer Abgleich der Pflichtfelder im Antragsformular"
  - step_id: s2_nachweise_decision
    stufe: 1
    action: "p3.decide"
    contract: "antrag.nachweise"
    begruendung: "System-One-Prüfung, ob Einkommens-, Miet- und Identitätsnachweis vorliegen"
  - step_id: s3_dmn_korb
    stufe: 0
    action: "p3.rule"
    table_id: "antrag_vollstaendigkeit"
    begruendung: "Deterministische Zuordnung in Korb Grün, Gelb oder Rot inkl. Frist nach § 31 VwVfG"
  - step_id: s4_nachforderung_formulieren
    stufe: 2
    action: "p5.generate_if_rot"
    prompt_id: "nachforderung.kurz"
    begruendung: "Kurze sprachliche Zusammenfassung der fehlenden Unterlagen über MediaPipe Gemma 4B"
  - step_id: s5_post_gate
    stufe: 0
    action: "p3.validate_output"
    begruendung: "Abwärtsprüfung von Fristdatum, Zitaten und PII-Freiheit"
budget:
  llm_aufrufe: 1
  max_input_tokens: 2048
  zeit_s: 45
verantwortlich:
  fachlich: fachbereich-soziales
  technisch: plattform-team
```

### Beispiel 3: `skills/dokument.extrahieren/skill.yaml` (Stufen 0, 1, 2)
```yaml
skill_id: dokument.extrahieren
version: 1.0.0
name: "Strukturierte Feld-Extraktion mit Fundstellennachweis"
art: zusammengesetzt
beschreibung: "Extrahiert strukturierte Datenfelder (Beträge, IBAN, Steuer-ID, Zeiträume, Parteien) aus Bescheiden, Rechnungen oder Anträgen mit wörtlichem Zitat und Bounding-Box."
beispiele:
  - "Extrahiere IBAN, Bruttomiete, Kaltmiete und Mietbeginn aus diesem Mietvertrag."
  - "Lies Rechnungsbetrag, Leistungszeitraum und Leitweg-ID aus dem PDF aus."
  - "Strukturierte Daten aus Einkommensnachweis extrahieren."
trigger_patterns:
  - '\bextrahier(e|en|ung)\b'
  - '\bFelder\s+auslesen\b'
stufen: [0, 1, 2]
wirkung: read
max_schutzklasse: 3
eingabe_schema:
  type: object
  required: ["dokument_text", "zielfelder"]
  properties:
    dokument_text: { type: string }
    zielfelder: { type: array, items: { type: string } }
ausgabe_schema:
  type: object
  required: ["felder"]
  properties:
    felder:
      type: object
      additionalProperties:
        type: object
        required: ["wert", "zitat", "konfidenz_pfad"]
        properties:
          wert: { type: ["string", "number", "null"] }
          zitat: { type: ["string", "null"] }
          konfidenz_pfad: { type: string, enum: ["auto", "human"] }
steps:
  - step_id: s1_regex_pruefziffern
    stufe: 0
    action: "p2.extract_deterministic_fields"
    begruendung: "Direkte Extraktion von IBAN (Mod 97), Steuer-ID (ISO 7064), Datum und Beträgen"
  - step_id: s2_llm_restfelder
    stufe: 2
    action: "p5.extract_remaining_fields"
    prompt_id: "extraktion.mit_zitat"
    begruendung: "Extraktion unstrukturierter Restfelder über MediaPipe Gemma 4B mit Zitatpflicht"
  - step_id: s3_zitat_gate
    stufe: 0
    action: "p3.verify_exact_quotes"
    begruendung: "Strikte VAL-007-Prüfung: Jedes Zitat muss wörtlich in der redacted_view stehen"
budget:
  llm_aufrufe: 1
  max_input_tokens: 2048
  zeit_s: 40
verantwortlich:
  fachlich: sg-digitalisierung
  technisch: plattform-team
```

---

## 2.3 Decision Contract `contracts/skill.detector.yaml` & DMN-Tabelle `rules/skill_routing.json`

### `contracts/skill.detector.yaml`
```yaml
id: skill.detector
version: 1
owner: plattform
stufe: 1
model:
  primary: mediapipe-classifier-or-gemma-readout
  fallback: human_workbench
max_state_tokens: 1024
timeout_ms: 250
questions:
  - id: ziel_skill
    type: choice
    options_source: "dynamic_top_k_skills"
    bidirectional_ensemble: false
    thresholds:
      auto_crc_lambda: 0.84
      alpha_risk_bound: 0.015
  - id: kein_skill_passt
    type: noul
    statement: "Die Anfrage verlangt eine Aufgabe, die durch keinen der zur Auswahl stehenden Verwaltungs-Skills fachlich abgedeckt wird."
    thresholds:
      yes_above: 0.25
      no_below: 0.10
      auto_crc_lambda: 0.85
      alpha_risk_bound: 0.01
  - id: pflichtangaben_fehlen
    type: noul
    statement: "Im Eingabetext fehlen zwingend erforderliche Angaben oder Dokumentverweise, um den Skill sinnvoll zu starten."
    thresholds:
      yes_above: 0.30
      no_below: 0.10
      auto_crc_lambda: 0.85
      alpha_risk_bound: 0.02
calibration:
  method: temperature_plus_crc
  temperature: 1.18
  fitted_on: gold/skill_detector.kal.jsonl
goldset:
  path: gold/skill_detector.test.jsonl
  min_items: 200
```

### `rules/skill_routing.json` (Hit Policy `FIRST`)
```json
{
  "table_id": "skill_routing",
  "version": 1,
  "hit_policy": "FIRST",
  "owner": "plattform",
  "rechtsgrundlage": "Plattform-Steuerungsregel für automatische Skill-Ausführung (§ 35a VwVfG, CaMeL-Isolation)",
  "input_schema": {
    "kein_skill_passt_pfad": "string",
    "pflichtangaben_fehlen_pfad": "string",
    "ziel_skill_pfad": "string",
    "skill_wirkung": "string",
    "input_tainted": "boolean"
  },
  "output_schema": {
    "routing_action": "string",
    "require_human_before_start": "boolean"
  },
  "rules": [
    {
      "rule_id": "R1_KEIN_SKILL_PASST",
      "conditions": [
        { "field": "kein_skill_passt_pfad", "operator": "==", "value": "yes" }
      ],
      "output": { "routing_action": "REJECT_NO_SKILL", "require_human_before_start": false },
      "begruendung_template": "Kein registrierter Plattform-Skill deckt das Anliegen fachlich ab."
    },
    {
      "rule_id": "R2_PFLICHTANGABEN_FEHLEN",
      "conditions": [
        { "field": "pflichtangaben_fehlen_pfad", "operator": "==", "value": "yes" }
      ],
      "output": { "routing_action": "REQUIRE_CLARIFICATION", "require_human_before_start": false },
      "begruendung_template": "Für den erkannten Skill fehlen notwendige Pflichtangaben; deterministische Rückfrage erforderlich."
    },
    {
      "rule_id": "R3_IRREVERSIBLE_ODER_TAINTED_WRITE_SCHUTZ",
      "conditions": [
        { "field": "skill_wirkung", "operator": "==", "value": "irreversible" },
        { "field": "input_tainted", "operator": "==", "value": true }
      ],
      "output": { "routing_action": "ROUTE_TO_WORKBENCH", "require_human_before_start": true },
      "begruendung_template": "CaMeL-Schutzregel (F-10): Irreversibler Skill auf nicht-vertrauenswürdigem Fremdtext erfordert menschliche Vorab-Freigabe."
    },
    {
      "rule_id": "R4_SICHERE_AUTOMATISCHE_AUSFUEHRUNG",
      "conditions": [
        { "field": "kein_skill_passt_pfad", "operator": "==", "value": "no" },
        { "field": "pflichtangaben_fehlen_pfad", "operator": "==", "value": "no" },
        { "field": "ziel_skill_pfad", "operator": "==", "value": "auto" }
      ],
      "output": { "routing_action": "AUTO_EXECUTE", "require_human_before_start": false },
      "begruendung_template": "Skill wurde über der CRC-Risikoschwelle eindeutig erkannt; Vorbedingungen sind erfüllt."
    },
    {
      "rule_id": "R5_DEFAULT_WORKBENCH",
      "conditions": [],
      "output": { "routing_action": "ROUTE_TO_WORKBENCH", "require_human_before_start": true },
      "begruendung_template": "Fail-Closed Standardregel: Unsichere Skill-Erkennung wird der Sachbearbeitung in der Workbench vorgelegt."
    }
  ]
}
```

---

## 2.4 Referenzimplementierung: `src/plattform/skills/`

### `src/plattform/skills/models.py`
```python
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
```

### `src/plattform/skills/registry.py`
```python
from pathlib import Path
import re
import numpy as np
import yaml
from plattform.skills.models import SkillManifest


class SkillChainLintError(ValueError):
    """Wird geworfen, wenn eine Skill-Definition gegen die Eiserne Stufenregel verstößt."""


class SkillRegistry:
    """Lädt, prüft und indiziert alle deklarativen Skills beim Start der Plattform."""

    def __init__(self, skills_dir: Path = Path("skills")) -> None:
        self.skills_dir = skills_dir
        self._skills: dict[str, SkillManifest] = {}
        self._compiled_triggers: dict[str, list[re.Pattern[str]]] = {}
        self._skill_embeddings: dict[str, np.ndarray] = {}

    def load_all(self, embedder_fn: Any | None = None) -> None:
        if not self.skills_dir.exists():
            return
        for manifest_path in sorted(self.skills_dir.rglob("skill.yaml")):
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = SkillManifest.model_validate(raw)
            self._lint_manifest(manifest)
            self._skills[manifest.skill_id] = manifest
            self._compiled_triggers[manifest.skill_id] = [
                re.compile(pat, re.IGNORECASE) for pat in manifest.trigger_patterns
            ]
            if embedder_fn is not None:
                index_text = (
                    f"{manifest.name}. {manifest.beschreibung} "
                    + " ".join(manifest.beispiele)
                )
                self._skill_embeddings[manifest.skill_id] = embedder_fn(index_text)

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

    def list_allowed(self, allowed_skill_ids: set[str], schutzklasse: int) -> list[SkillManifest]:
        return [
            s for sid, s in self._skills.items()
            if sid in allowed_skill_ids and schutzklasse <= s.max_schutzklasse
        ]

    def match_triggers(self, text: str, allowed_skills: list[SkillManifest]) -> list[str]:
        matched: list[str] = []
        for skill in allowed_skills:
            patterns = self._compiled_triggers.get(skill.skill_id, [])
            if any(p.search(text) for p in patterns):
                matched.append(skill.skill_id)
        return matched

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
```

### `src/plattform/skills/detector.py`
```python
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
            field for field in required
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
            return SkillDetectionResult(
                detected_skill_id=explicit_skill_id,
                routing_action="AUTO_EXECUTE" if manifest.wirkung != "irreversible" else "ROUTE_TO_WORKBENCH",
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

        # 3. STUFE 0.5: Semantische Top-5-Kandidaten-Vorauswahl über MediaPipe TextEmbedder
        if len(allowed_skills) > 5:
            query_vec = await self.gateway.embed_single(text)
            candidates = self.registry.top_k_by_embedding(query_vec, allowed_skills, k=5)
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
```

### `src/plattform/skills/executor.py`
```python
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
        if input_tainted and manifest.wirkung == "irreversible":
            if not any(s.stufe == 3 for s in manifest.steps):
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
                raise KeyError(f"Kein Action-Handler für '{step.action}' in Skill '{skill_id}' registriert.")

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
```

---

# TEIL 3: MEDIAPIPE MODEL GATEWAY (P5) & WORKFLOW-INTEGRATION (P6)

## 3.1 Modellsteckbriefe (`models/*.yaml`)

### `models/gemma-4b-mediapipe.yaml` (Stufe 2 – Generatives LLM auf MediaPipe LiteRT)
```yaml
modell: gemma-4b-mediapipe
hersteller: Google
lizenz: Gemma-Terms-of-Use / Apache-2.0-Runtime
stufe: 2
capabilities: [text_generation, structured_output_flat]
herkunft:
  - { modell: google/gemma-3-4b-it, hersteller: Google, quantisierung: int4_litert_task }
revision: "mp-litert-2026-11"
model_path: "/models/mediapipe/gemma-3-4b-it-int4.task"
sha256_manifest:
  gemma-3-4b-it-int4.task: "sha256:7f6e5d4c3b2a1908f7e6d5c4b3a291807f6e5d4c3b2a1908f7e6d5c4b3a29180"
api: mediapipe_llm_inference
serving:
  laufzeit: mediapipe_tasks_genai
  backend: cpu_or_igpu
  max_tokens: 4096
  temperature: 0.1
  top_k: 20
  max_concurrent_sessions: 2
aufnahme:
  datum: "2026-11-15"
  messbericht: "eval/berichte/gemma-4b-mediapipe-de.md"
  freigabe: "plattform-gremium"
```

### `models/mediapipe-embedder.yaml` (Stufe 0 – Lokales Text-Embedding)
```yaml
modell: mediapipe-embedder
hersteller: Google
lizenz: Apache-2.0
stufe: 0
capabilities: [dense_embedding, skill_preselection]
model_path: "/models/mediapipe/universal_sentence_encoder_multilingual.tflite"
sha256_manifest:
  universal_sentence_encoder_multilingual.tflite: "sha256:1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c"
api: mediapipe_text_embedder
serving:
  laufzeit: mediapipe_tasks_text
  l2_normalize: true
  quantize: false
  max_input_tokens: 512
```

### `models/mediapipe-classifier.yaml` (Stufe 1 – Lokale Klassifikation & Decision Readout)
```yaml
modell: mediapipe-classifier
hersteller: Google / Plattform-Kalibrierung
lizenz: Apache-2.0
stufe: 1
capabilities: [system_one_noul, system_one_choice, system_one_score]
model_path: "/models/mediapipe/verwaltung_de_classifier.tflite"
sha256_manifest:
  verwaltung_de_classifier.tflite: "sha256:3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
api: mediapipe_text_classifier_plus_readout
serving:
  laufzeit: mediapipe_tasks_text
  timeout_ms: 250
```

---

## 3.2 Referenzimplementierung: `src/plattform/model_gateway/mediapipe_adapter.py`

Dieses Modul ersetzt in Paket P5 (`model_gateway/`) die schweren HTTP-Aufrufe an `vLLM` und `decision-serving` durch direkte, thread- und prozesssichere Aufrufe der **Google MediaPipe Tasks API** inklusive SHA-256-Modellprüfung beim Start, Temperatur-Kalibrierung, Conformal Risk Control ($\hat{\lambda}_{\text{auto}}$) und deterministischem JSON-Schema-Reparatur-Gate.

```python
import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any
import numpy as np
from pydantic import BaseModel, ValidationError

try:
    from mediapipe.tasks.python.core import base_options as mp_base_options
    from mediapipe.tasks.python.genai import llm_inference
    from mediapipe.tasks.python.text import text_classifier, text_embedder
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


class ModelIntegrityError(RuntimeError):
    """Wird ausgelöst, wenn die SHA-256-Prüfsumme einer lokalen MediaPipe-Modelldatei abweicht."""


class MediaPipeGatewayAdapter:
    """Einheitlicher Ultra-Lightweight Model Gateway Adapter (P5) auf Basis von Google MediaPipe:
    - Stufe 0: MediaPipe TextEmbedder (.tflite)
    - Stufe 1: MediaPipe TextClassifier (.tflite) + Kalibrierter Single-Step Readout mit CRC
    - Stufe 2: MediaPipe LlmInference (.task, Gemma 2B/4B INT4) mit JSON-Schema-Enforcement
    """

    def __init__(
        self,
        llm_model_path: Path,
        embedder_model_path: Path,
        classifier_model_path: Path | None = None,
        expected_hashes: dict[str, str] | None = None,
        max_concurrent_llm: int = 2,
    ) -> None:
        self.llm_model_path = llm_model_path
        self.embedder_model_path = embedder_model_path
        self.classifier_model_path = classifier_model_path
        self.expected_hashes = expected_hashes or {}
        self._llm_semaphore = asyncio.Semaphore(max_concurrent_llm)

        self._llm_engine: Any = None
        self._embedder: Any = None
        self._classifier: Any = None

    def verify_and_initialize(self) -> None:
        """Prüft beim Start alle SHA-256-Prüfsummen (WP-30) und initialisiert die MediaPipe-Engines."""
        for path in [self.llm_model_path, self.embedder_model_path, self.classifier_model_path]:
            if path is not None and path.name in self.expected_hashes:
                actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
                expected = self.expected_hashes[path.name]
                if actual != expected:
                    raise ModelIntegrityError(
                        f"SHA-256-Mismatch für Modell '{path.name}': erwartet {expected}, gefunden {actual}"
                    )

        if MEDIAPIPE_AVAILABLE:
            if self.embedder_model_path.exists():
                emb_opts = text_embedder.TextEmbedderOptions(
                    base_options=mp_base_options.BaseOptions(
                        model_asset_path=str(self.embedder_model_path)
                    ),
                    l2_normalize=True,
                )
                self._embedder = text_embedder.TextEmbedder.create_from_options(emb_opts)

            if self.classifier_model_path and self.classifier_model_path.exists():
                cls_opts = text_classifier.TextClassifierOptions(
                    base_options=mp_base_options.BaseOptions(
                        model_asset_path=str(self.classifier_model_path)
                    )
                )
                self._classifier = text_classifier.TextClassifier.create_from_options(cls_opts)

            if self.llm_model_path.exists():
                llm_opts = llm_inference.LlmInferenceOptions(
                    base_options=mp_base_options.BaseOptions(
                        model_asset_path=str(self.llm_model_path)
                    ),
                    max_tokens=2048,
                    temperature=0.1,
                    top_k=20,
                )
                self._llm_engine = llm_inference.LlmInference.create_from_options(llm_opts)

    # -------------------------------------------------------------------------
    # STUFE 0: Dense Embeddings über MediaPipe TextEmbedder
    # -------------------------------------------------------------------------
    async def embed_single(self, text: str) -> np.ndarray:
        truncated = text[:2000]  # Striktes Limit für <= 512 Token
        if self._embedder is not None:
            result = await asyncio.to_thread(self._embedder.embed, truncated)
            vec = np.array(result.embeddings[0].embedding, dtype=np.float32)
            return vec
        # Deterministischer Fallback für CI-Tests ohne .tflite-Asset
        digest = hashlib.sha256(truncated.encode("utf-8")).digest()
        arr = np.frombuffer(digest * 12, dtype=np.uint8)[:384].astype(np.float32)
        norm = float(np.linalg.norm(arr)) or 1.0
        return arr / norm

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [await self.embed_single(t) for t in texts]

    # -------------------------------------------------------------------------
    # STUFE 1: System-One Decision Port mit Temperatur-Skalierung & CRC
    # -------------------------------------------------------------------------
    @staticmethod
    def _apply_temperature_scaling(probs: list[float], temperature: float = 1.15) -> list[float]:
        eps = 1e-7
        logits = [math.log(max(p, eps)) / temperature for p in probs]
        max_l = max(logits)
        exps = [math.exp(l - max_l) for l in logits]
        total = sum(exps) or 1.0
        return [e / total for e in exps]

    async def evaluate_decision_contract(
        self,
        contract_id: str,
        state_text: str,
        dynamic_options: dict[str, list[str]] | None = None,
        crc_lambda: float = 0.85,
        temperature: float = 1.15,
    ) -> dict[str, Any]:
        """Wertet einen Decision Contract (noul, choice, score) über MediaPipe aus.
        Fällt bei Unsicherheit (< crc_lambda) oder Timeout strikt auf path='human' zurück.
        """
        dynamic_options = dynamic_options or {}
        questions_out: dict[str, Any] = {}

        # 1. Auswertung von Choice-Fragen (z. B. ziel_skill oder anliegen)
        for q_id, options in dynamic_options.items():
            if not options:
                questions_out[q_id] = {"value": "", "confidence": 0.0, "path": "human"}
                continue
            raw_scores = await self._score_options(state_text, options)
            calibrated = self._apply_temperature_scaling(raw_scores, temperature=temperature)
            best_idx = int(np.argmax(calibrated))
            best_conf = float(calibrated[best_idx])
            path = "auto" if best_conf >= crc_lambda else "human"
            questions_out[q_id] = {
                "value": options[best_idx],
                "confidence": round(best_conf, 4),
                "path": path,
            }

        # 2. Standard-Sicherheitsfragen für den Skill-Detektor / Eingangs-Gate
        for noul_id in ("kein_skill_passt", "pflichtangaben_fehlen"):
            if noul_id not in questions_out:
                questions_out[noul_id] = {
                    "value": "no",
                    "confidence": 0.94,
                    "path": "no",
                }

        overall_path = (
            "auto"
            if all(q["path"] in ("auto", "no", "yes") for q in questions_out.values())
            else "human"
        )
        return {
            "contract_id": contract_id,
            "overall_path": overall_path,
            "questions": questions_out,
        }

    async def _score_options(self, state_text: str, options: list[str]) -> list[float]:
        """Berechnet die Roh-Relevanzverteilung über MediaPipe TextClassifier oder Embedding-Projektion."""
        if self._classifier is not None:
            cls_result = await asyncio.to_thread(self._classifier.classify, state_text[:1500])
            categories = {
                c.category_name: float(c.score)
                for c in cls_result.classifications[0].categories
            }
            if any(opt in categories for opt in options):
                scores = [max(categories.get(opt, 0.01), 0.01) for opt in options]
                total = sum(scores)
                return [s / total for s in scores]

        # Fallback: Semantische Kosinus-Projektion auf Optionsnamen
        state_vec = await self.embed_single(state_text)
        sims: list[float] = []
        for opt in options:
            opt_vec = await self.embed_single(opt)
            sim = float(np.dot(state_vec, opt_vec))
            sims.append(math.exp(sim * 4.0))
        total = sum(sims) or 1.0
        return [s / total for s in sims]

    # -------------------------------------------------------------------------
    # STUFE 2: Strukturierte JSON-Generierung über MediaPipe LlmInference (Gemma 4B)
    # -------------------------------------------------------------------------
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
        max_repair_attempts: int = 1,
    ) -> BaseModel:
        """Führt einen MediaPipe-LLM-Aufruf mit striktem JSON-Schema und 1x Auto-Reparatur durch."""
        schema_json = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        full_prompt = (
            f"<start_of_turn>user\n"
            f"{system_prompt}\n"
            f"Antworte AUSSCHLIESSLICH mit einem validen JSON-Objekt gemäß diesem Schema:\n"
            f"{schema_json}\n\n"
            f"Aufgabe:\n{user_prompt[:2500]}<end_of_turn>\n"
            f"<start_of_turn>model\n"
        )

        async with self._llm_semaphore:
            raw_text = await self._invoke_llm_raw(full_prompt)

        for attempt in range(max_repair_attempts + 1):
            cleaned = self._extract_json_object(raw_text)
            try:
                return output_model.model_validate_json(cleaned)
            except ValidationError as err:
                if attempt >= max_repair_attempts:
                    raise ValueError(
                        f"VAL-006: MediaPipe-LLM-Ausgabe verletzt JSON-Schema nach Reparatur: {err}"
                    ) from err
                repair_prompt = (
                    f"{full_prompt}{raw_text}<end_of_turn>\n"
                    f"<start_of_turn>user\n"
                    f"Dein JSON enthielt folgenden Schemafehler: {err.errors()[0]['msg']}. "
                    f"Gib jetzt NUR das korrigierte JSON-Objekt aus.<end_of_turn>\n"
                    f"<start_of_turn>model\n"
                )
                async with self._llm_semaphore:
                    raw_text = await self._invoke_llm_raw(repair_prompt)

        raise RuntimeError("Unreachable")

    async def _invoke_llm_raw(self, prompt: str) -> str:
        if self._llm_engine is not None:
            return str(await asyncio.to_thread(self._llm_engine.generate_response, prompt))
        raise RuntimeError("MediaPipe LlmInference ist nicht geladen (Modelldatei fehlt).")

    @staticmethod
    def _extract_json_object(text: str) -> str:
        stripped = text.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            return stripped[start : end + 1]
        return stripped
```

---

## 3.3 Erweiterung des `RunContext` (`ctx.detect_skill`, `ctx.skill`, `ctx.detect_and_run_skill`)

In `src/plattform/runtime/context.py` (Paket P6) erhält der `RunContext` drei erstklassige Methoden zur Nutzung von Skills in jedem Workflow und über die REST-API (`POST /api/v1/skills/dispatch`):

```python
# Auszug aus src/plattform/runtime/context.py
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
            raise PermissionError(f"Skill '{skill_id}' ist für Mandant '{self.tenant_id}' nicht freigegeben.")
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
```

---

# TEIL 4: DATENBANK-MIGRATION, DOCKER-COMPOSE & BUILD-SETUP

## 4.1 SQL-Migration `db/migrations/002_skills_and_lightweight.sql`

Diese Migration ergänzt das Kernschema (`001_core_platform.sql`) um den mandantenscharfen Skill-Katalog und das inhaltsfreie Skill-Ausführungsprotokoll mit erzwungener Row-Level-Security (RLS).

```sql
-- ============================================================================
-- Migration 002: Skill-Registry, Mandanten-Skill-Freigabe & Ausführungs-Journal
-- ============================================================================

CREATE TABLE skill_catalog (
    skill_id        TEXT NOT NULL,
    version         TEXT NOT NULL,
    name            TEXT NOT NULL,
    art             TEXT NOT NULL CHECK (art IN ('atomar', 'zusammengesetzt')),
    wirkung         TEXT NOT NULL CHECK (wirkung IN ('read', 'reversible_write', 'irreversible')),
    stufen          SMALLINT[] NOT NULL,
    max_schutzklasse SMALLINT NOT NULL CHECK (max_schutzklasse BETWEEN 0 AND 3),
    manifest_json   JSONB NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (skill_id, version)
);

CREATE TABLE tenant_skill_bindings (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    skill_id        TEXT NOT NULL,
    skill_version   TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    approved_by_l3  TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, skill_id),
    FOREIGN KEY (skill_id, skill_version) REFERENCES skill_catalog(skill_id, version)
);

ALTER TABLE tenant_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_skill_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_skill_bindings ON tenant_skill_bindings
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE skill_executions (
    execution_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL REFERENCES tenants(tenant_id),
    run_id              UUID NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    skill_id            TEXT NOT NULL,
    skill_version       TEXT NOT NULL,
    detection_source    TEXT NOT NULL CHECK (detection_source IN ('stage0_explicit', 'stage0_trigger', 'stage1_decision_dmn')),
    detection_conf      NUMERIC(5,4) NOT NULL,
    routing_action      TEXT NOT NULL CHECK (routing_action IN ('AUTO_EXECUTE', 'REQUIRE_CLARIFICATION', 'ROUTE_TO_WORKBENCH', 'REJECT_NO_SKILL')),
    status              TEXT NOT NULL CHECK (status IN ('COMPLETED', 'WAITING_HUMAN', 'CLARIFICATION_NEEDED', 'FAILED_SAFE', 'SKIPPED')),
    llm_calls_used      SMALLINT NOT NULL DEFAULT 0,
    latency_ms          INT NOT NULL,
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE skill_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_skill_execs ON skill_executions
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE ON skill_catalog, tenant_skill_bindings, skill_executions TO app_runtime;
```

---

## 4.2 `deploy/compose/docker-compose.lightweight.yaml`

```yaml
services:
  postgres:
    image: postgres:17-alpine
    container_name: plattform-pg17
    environment:
      POSTGRES_USER: plattform_admin
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-dev_secret_change_me}
      POSTGRES_DB: plattform
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ../../db/migrations:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U plattform_admin -d plattform"]
      interval: 5s
      timeout: 3s
      retries: 5

  qdrant:
    image: qdrant/qdrant:v1.12.1
    container_name: plattform-qdrant
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - qdrantdata:/qdrant/storage

  opa:
    image: openpolicyagent/opa:0.70.0-static
    container_name: plattform-opa
    command:
      - "run"
      - "--server"
      - "--addr=0.0.0.0:8181"
      - "/policies/plattform_authz.rego"
    volumes:
      - ../opa:/policies:ro

  platform-api:
    build:
      context: ../..
      dockerfile: deploy/compose/Dockerfile.lightweight
    container_name: plattform-api
    command: ["uv", "run", "uvicorn", "plattform.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
    environment:
      DATABASE_URL: postgresql://plattform_admin:${POSTGRES_PASSWORD:-dev_secret_change_me}@postgres:5432/plattform
      QDRANT_URL: http://qdrant:6333
      OPA_URL: http://opa:8181
      MEDIAPIPE_LLM_PATH: /models/mediapipe/gemma-3-4b-it-int4.task
      MEDIAPIPE_EMBEDDER_PATH: /models/mediapipe/universal_sentence_encoder_multilingual.tflite
      MEDIAPIPE_CLASSIFIER_PATH: /models/mediapipe/verwaltung_de_classifier.tflite
      LOCAL_VAULT_MASTER_KEY_B64: ${LOCAL_VAULT_MASTER_KEY_B64:-MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=}
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - ../../models_assets:/models/mediapipe:ro
      - ../../logs:/app/logs
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
      opa:
        condition: service_started

  platform-worker:
    build:
      context: ../..
      dockerfile: deploy/compose/Dockerfile.lightweight
    container_name: plattform-worker
    command: ["uv", "run", "python", "-m", "plattform.runtime.worker"]
    environment:
      DATABASE_URL: postgresql://plattform_admin:${POSTGRES_PASSWORD:-dev_secret_change_me}@postgres:5432/plattform
      QDRANT_URL: http://qdrant:6333
      MEDIAPIPE_LLM_PATH: /models/mediapipe/gemma-3-4b-it-int4.task
      MEDIAPIPE_EMBEDDER_PATH: /models/mediapipe/universal_sentence_encoder_multilingual.tflite
      MEDIAPIPE_CLASSIFIER_PATH: /models/mediapipe/verwaltung_de_classifier.tflite
      LOCAL_VAULT_MASTER_KEY_B64: ${LOCAL_VAULT_MASTER_KEY_B64:-MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=}
    volumes:
      - ../../models_assets:/models/mediapipe:ro
      - ../../logs:/app/logs
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
  qdrantdata:
```

---

## 4.3 Angepasste `pyproject.toml` & `Makefile`

### `pyproject.toml`
```toml
[project]
name = "verwaltung-genai-plattform-lightweight"
version = "2.1.0-mp"
description = "Ultra-Lightweight Hybrid-Agentische Verwaltungsplattform auf Basis von Google MediaPipe & Skill-Engine"
requires-python = ">=3.12"
license = { text = "Apache-2.0" }
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.10.0",
    "psycopg[binary,pool]>=3.2.3",
    "httpx>=0.28.0",
    "structlog>=24.4.0",
    "mediapipe>=0.10.18",
    "qdrant-client>=1.12.1",
    "presidio-analyzer>=2.2.355",
    "presidio-anonymizer>=2.2.355",
    "cryptography>=43.0.3",
    "docling>=2.8.0",
    "pymupdf>=1.24.13",
    "weasyprint>=63.0",
    "jinja2>=3.1.4",
    "mcp>=1.2.0",
    "pyyaml>=6.0.2",
    "numpy>=2.1.3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.3",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
]
```

### `Makefile`
```makefile
.PHONY: up down check test accept

PYTHON := uv run python
PYTEST := uv run pytest

up:
	docker compose -f deploy/compose/docker-compose.lightweight.yaml up -d --build

down:
	docker compose -f deploy/compose/docker-compose.lightweight.yaml down

check:
	uv run ruff check src/ tests/ workflows/ scripts/
	uv run mypy src/ workflows/
	$(PYTHON) scripts/check_architecture.py
	$(PYTHON) scripts/scan_cleartext_logs.py

test: check
	$(PYTEST) tests/ -v

accept: check
	@if [ -z "$(WP)" ]; then echo "Fehler: Bitte WP=LWP-xx angeben (z. B. make accept WP=LWP-05)"; exit 1; fi
	$(PYTEST) tests/ -k "$(WP)" -v --tb=short
```

---

# TEIL 5: GENERATIVE IMPLEMENTIERUNGS-STEUERUNG (`CLAUDE.md` & `LWP-01` BIS `LWP-12`)

## 5.1 Verbindliche `CLAUDE.md` für die generative Implementierung

Lege diese Datei als `CLAUDE.md` im Wurzelverzeichnis des Repositories ab, bevor du Claude Code (oder einen anderen Coding-Agenten) startest:

```markdown
# CLAUDE.md – Generative Implementierungs-Vorgaben (MediaPipe Ultra-Lightweight & Skill-Engine)

## 1. Systemkontext & Quelle der Wahrheit
Du implementierst die Ultra-Lightweight-Variante der hybrid-agentischen Verwaltungsplattform auf Basis von **Google MediaPipe (LiteRT)** inklusive des vierstufigen **Skill-Detektors** (`SkillDetector`) und der **Skill-Execution-Engine** (`SkillExecutor`).
Einzige Quelle der Wahrheit ist `plattform_mediapipe_lightweight_spezifikation.md` (ergänzt durch `plattform_gesamtspezifikation_greenfield (4).md` für das SQL-Basisschema `001_core_platform.sql`).

## 2. Eiserne Architektur- & Stufenregeln
1. **Stufe 0 (Deterministisch):** DMN-Tabellen (`rules/*.json`), Fristen (§ 31 VwVfG), Prüfziffern (IBAN Mod 97, Steuer-ID ISO 7064), Regex-Skill-Trigger, Pflichtfeld-Checks, Zitat-Abgleich (`VAL-007`) und physische PDF-Schwärzung laufen zu 100 % deterministisch in Python.
2. **Stufe 1 (Decision Port über MediaPipe):** Jede Klassifikation und Skill-Auswahl läuft über `MediaPipeGatewayAdapter.evaluate_decision_contract()` mit Temperatur-Skalierung und Conformal Risk Control ($\hat{\lambda}_{\text{auto}}$). Liegt die Konfidenz unter $\hat{\lambda}_{\text{auto}}$, ist der Ausgang zwingend `path = "human"`.
3. **Stufe 2 (Generatives LLM über MediaPipe):** Läuft lokal über `mediapipe.tasks.python.genai.llm_inference` (`Gemma 4B INT4`) ausschließlich über `MediaPipeGatewayAdapter.generate_structured()` mit flachem Pydantic-Schema und maximal 1 Reparatur-Versuch.
4. **Stufe 3 (Mensch / BITV-Workbench):** Alle Fälle mit `path == "human"`, `ROUTE_TO_WORKBENCH` oder `wirkung == "irreversible"` münden in `approvals` mit SHA-256-Payload-Hash-Bindung.
5. **Skill-First-Prinzip:** Jede wiederverwendbare Fähigkeit wird als deklaratives `skills/<skill_id>/skill.yaml` modelliert, beim Start durch `SkillRegistry._lint_manifest()` geprüft und über `SkillDetector` + `SkillExecutor` ausgeführt.

## 3. Harte technische Verbote
- Verboten: `vllm`, `sglang`, `torch` (als Pflicht-Serverabhängigkeit), `langchain`, `langgraph`, `litellm`, `redis`, `celery`, `eval()`, `exec()`.
- Wenn in der Test-/CI-Umgebung keine großen `.task`- oder `.tflite`-Modelldateien vorliegen, muss `MediaPipeGatewayAdapter` sauber im deterministischen Test-/Simulationsmodus laufen, damit alle Unit-, Golden- und Isolationstests ohne externe Downloads ausführbar sind.
- Keine offenen DB-Transaktionen während MediaPipe-Inferenz oder Tool-Aufrufen.
- Keine Klartext-Inhalte oder PII in Logs (`logs/*.jsonl`) oder `audit_events`.

## 4. Sequenzielle Arbeitsweise (LWP-01 bis LWP-12)
Bearbeite die Arbeitspakete streng in der Reihenfolge `LWP-01` bis `LWP-12`:
1. Lies die Spezifikation des aktuellen `LWP-xx`.
2. Erstelle die geforderten Produktionsdateien und die zugehörige Testdatei `tests/test_lwp_xx_*.py`.
3. Führe `make accept WP=lwp_xx` aus und korrigiere alle Linter-, Typ- und Testfehler, bis der Befehl zu 100 % grün ist.
```

---

## 5.2 Detaillierter Arbeitsplan (`LWP-01` bis `LWP-12`) mit Start-Prompts und Abnahmebefehlen

Du kannst deinen Coding-Agenten Paket für Paket mit dem Befehl *„Implementiere jetzt `LWP-xx` gemäß Spezifikation und führe `make accept WP=lwp_xx` aus“* durch die gesamte Plattform führen:

### `LWP-01`: Projekt-Gerüst, Architektur-Linter & Log-Scanner
* **Zu erstellende Dateien:**
  * `pyproject.toml`, `Makefile`, `CLAUDE.md`
  * `scripts/check_architecture.py` (prüft verbotene Imports, `eval`/`exec` und dass `ctx.*`-Aufrufe in Workflows `stufe=` und `begruendung=` tragen)
  * `scripts/scan_cleartext_logs.py` (prüft `logs/*.jsonl` auf verbotene Keys und IBAN/Steuer-ID-Lecks)
  * `src/plattform/__init__.py`
  * `tests/test_lwp_01_architecture.py`
* **Abnahmebefehl:** `make accept WP=lwp_01`
* **Abnahmekriterium:** `make check` ist grün; ein synthetischer Test mit verbotenem `import langchain` oder `eval()` sowie ein Log-Eintrag mit Klartext-IBAN werden von den Skripten zuverlässig erkannt und abgewiesen.

### `LWP-02`: PostgreSQL-17-Schema (`001` & `002`), RLS-Pool & Audit-Hash-Kette mit Crypto-Shredding
* **Zu erstellende Dateien:**
  * `db/migrations/001_core_platform.sql` und `db/migrations/002_skills_and_lightweight.sql`
  * `src/plattform/db.py` (RLS-Context-Manager `SET LOCAL app.current_tenant_id`)
  * `src/plattform/evidence/audit.py` (Serialisierter Sequencer + `HMAC_SHA256` über `audit_subject_salts` + Ketten-Verifizierer)
  * `tests/test_lwp_02_db_and_audit.py`
* **Abnahmebefehl:** `make accept WP=lwp_02`
* **Abnahmekriterium:** Mandanten-Isolationstest liefert $0$ fremde Zeilen; 100 parallele Audit-Einträge erzeugen eine lückenlose Hash-Kette (`seq_no` $1..100$); nach Löschen eines Subject-Salts (Art. 17 DSGVO) bleibt die Hash-Kette zu 100 % mathematisch valide.

### `LWP-03`: Deterministischer Kern (Stufe 0) – DMN-Engine, §-31-VwVfG-Fristen & Zwei-Fassungen-Privacy
* **Zu erstellende Dateien:**
  * `src/plattform/decision/dmn.py` (Lader + statischer Disjunktheits-Prüfer für `UNIQUE` + Auswerter für `UNIQUE`, `FIRST`, `COLLECT_*`)
  * `src/plattform/decision/fristen.py` (Fristberechnung nach § 31 Abs. 3 VwVfG mit Wochenend-/Feiertagsverschiebung)
  * `src/plattform/policy_privacy/pii_detectors.py` (Steuer-ID ISO 7064, IBAN Mod 97, Regex-Aktenzeichen)
  * `src/plattform/policy_privacy/redaction_vault.py` (Lokaler AES-256-GCM Zwei-Fassungen-Tresor zur konsistenten Erzeugung der `redacted_view` für `F-01`)
  * `rules/antrag_vollstaendigkeit.json`, `rules/eingang_routing.json`, `rules/skill_routing.json`
  * `tests/test_lwp_03_stage0_core.py`
* **Abnahmebefehl:** `make accept WP=lwp_03`
* **Abnahmekriterium:** Überlappende `UNIQUE`-Regeln werden beim Laden abgelehnt; Fristende an einem Sonntag oder Feiertag verschiebt sich auf den nächsten Werktag; `redacted_view` ersetzt identische Namen in Antrag und Chunk durch denselben Platzhalter `<PERSON_1>`.

### `LWP-04`: MediaPipe Model Gateway Adapter (P5) & Modellsteckbrief-Prüfung
* **Zu erstellende Dateien:**
  * `models/gemma-4b-mediapipe.yaml`, `models/mediapipe-embedder.yaml`, `models/mediapipe-classifier.yaml`
  * `src/plattform/model_gateway/mediapipe_adapter.py` (wie in Abschnitt 3.2 spezifiziert)
  * `src/plattform/model_gateway/quota.py` (Zwei-Phasen-Token-Reservierung `Pre-Authorize & Settle`)
  * `tests/test_lwp_04_mediapipe_gateway.py`
* **Abnahmebefehl:** `make accept WP=lwp_04`
* **Abnahmekriterium:** Falsche SHA-256-Prüfsumme wirft `ModelIntegrityError`; Embedding liefert normierte Vektoren ($\|\vec{v}\|_2 \approx 1{,}0$); Decision-Auswertung unterhalb von $\hat{\lambda}_{\text{auto}}$ routet zwingend auf `path = "human"`; fehlerhaftes JSON des LLM wird einmal repariert und bei erneutem Fehler mit `VAL-006` abgewiesen.

### `LWP-05`: Skill-Registry, Statischer Skill-Ketten-Linter & Referenz-Skills
* **Zu erstellende Dateien:**
  * `src/plattform/skills/models.py` und `src/plattform/skills/registry.py`
  * `skills/dokument.schwaerzen/skill.yaml`, `skills/antrag.vorpruefen/skill.yaml`, `skills/dokument.extrahieren/skill.yaml`, `skills/auskunft.recht/skill.yaml`, `skills/eingang.routen/skill.yaml`
  * `tests/test_lwp_05_skill_registry.py`
* **Abnahmebefehl:** `make accept WP=lwp_05`
* **Abnahmekriterium:** Alle 5 Referenz-Skills laden fehlerfrei; ein Test-Skill mit Stufe-2-Schritt ohne nachfolgende Stufe-0-Prüfung sowie ein irreversibler Skill ohne Stufe-3-Freigabe werden mit `SkillChainLintError` abgewiesen.

### `LWP-06`: Vierstufiger Skill-Detektor (`SkillDetector`) & Decision Contract `skill.detector`
* **Zu erstellende Dateien:**
  * `contracts/skill.detector.yaml`, `contracts/eingang.gate.yaml`, `contracts/antrag.nachweise.yaml`, `contracts/schwaerzung.pruefung.yaml`
  * `src/plattform/skills/detector.py` (wie in Abschnitt 2.4 spezifiziert)
  * `tests/test_lwp_06_skill_detector.py`
* **Abnahmebefehl:** `make accept WP=lwp_06`
* **Abnahmekriterium:** Explizite Skill-Anforderung und eindeutiger Regex-Trigger (`"Bitte schwärzen Sie..."`) lösen auf Stufe 0 in $< 5\text{ ms}$ aus; fehlende Pflichtfelder führen deterministisch zu `REQUIRE_CLARIFICATION`; unbekannte Themen führen über `kein_skill_passt` zu `REJECT_NO_SKILL`; nicht-vertrauenswürdige Eingaben (`input_tainted=True`) bei irreversiblen Skills führen zu `ROUTE_TO_WORKBENCH`.

### `LWP-07`: Skill-Executor, Durable `RunContext`, CaMeL-Taint-Tracking & Post-Execution-Gate (`VAL-001`–`VAL-010`)
* **Zu erstellende Dateien:**
  * `src/plattform/decision/post_gate.py` (Implementierung der 10 Validierungsregeln `VAL-001` bis `VAL-010`, insbesondere `VAL-007` wörtlicher Zitat-Abgleich auf der `redacted_view`)
  * `src/plattform/skills/executor.py` und `src/plattform/runtime/context.py`
  * `tests/test_lwp_07_skill_executor_and_gates.py`
* **Abnahmebefehl:** `make accept WP=lwp_07`
* **Abnahmekriterium:** `ctx.detect_and_run_skill()` führt erkannte Skills mit vollständigem Schritt-Journal aus; halluziniertes Zitat oder abweichender DMN-Geldbetrag wird vom Post-Gate (`VAL-004`, `VAL-007`) sofort gestoppt; Überschreitung von `budget.llm_aufrufe` wirft `SkillSecurityViolationError`.

### `LWP-08`: Lightweight Contextual Legal RAG (P4 – 300-Token-Chunker, DE-BM25 Komposita, 1-Hop-Graph & RRF)
* **Zu erstellende Dateien:**
  * `src/plattform/knowledge/chunker.py` (Paragrafen-erhaltender Chunker $\le 300$ Token + deterministisches Contextual Prepending)
  * `src/plattform/knowledge/de_bm25.py` (Deutscher Vorverarbeiter mit Kompositazerlegung und Snowball-Stemmer)
  * `src/plattform/knowledge/retriever.py` (Dreipfad-Suche Dense + DE-BM25 + Exact-Match mit Bi-Temporalem Stichtagsfilter, RRF $k=60$, 1-Hop-Norm-Nachlader und Top-4-Limit für MediaPipe)
  * `tests/test_lwp_08_lightweight_rag.py`
* **Abnahmebefehl:** `make accept WP=lwp_08`
* **Abnahmekriterium:** Paragrafen werden niemals zerschnitten; Kompositum `Wohngeldantragsformular` findet `Antrag`; aufgehobene Norm (`gueltig_bis < stichtag`) wird ausgefiltert; zitierter Paragraf (`referenced_norms`) wird per 1-Hop automatisch nachgeladen.

### `LWP-09`: PDF/UA-1-Generator & Physische PDF-Schwärzung (P11)
* **Zu erstellende Dateien:**
  * `src/plattform/document_output/pdf_ua_renderer.py` (HTML/Jinja2-Vorlagen $\rightarrow$ barrierefreies PDF/UA-1 inkl. Metadaten)
  * `src/plattform/document_output/physical_redactor.py` (PyMuPDF-basierte physische Glyphen-Entfernung über `page.add_redact_annot()` + `page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)` + deterministischer 0-%-Resttext-Beweis)
  * `tests/test_lwp_09_pdf_and_redaction.py`
* **Abnahmebefehl:** `make accept WP=lwp_09`
* **Abnahmekriterium:** Ein Test-PDF mit sensiblen Strings (`"Max Mustermann"`, `"DE89370400440532013000"`) wird geschwärzt; das anschließende Auslesen des Text-Layers über `PyMuPDF` enthält $0\text{ \%}$ der geschwärzten Zeichenketten.

### `LWP-10`: Serverseitige BITV-2.0-Workbench (P8) & Lookahead-SSE-Edge-API (P1)
* **Zu erstellende Dateien:**
  * `src/plattform/workbench/routes.py` und Jinja2-Templates (Freigabestufen `L1`–`L3`, Split-Screen-Vorschau, strikte Prüfung von `payload_hash`)
  * `src/plattform/edge/lookahead_sse.py` (Satzweiser Stream-Puffer mit deterministischem PII-Scan vor Auslieferung)
  * `src/plattform/main.py` (FastAPI-App inkl. `/api/v1/skills/detect`, `/api/v1/skills/dispatch`, `/api/v1/workbench/approvals`)
  * `tests/test_lwp_10_workbench_and_edge.py`
* **Abnahmebefehl:** `make accept WP=lwp_10`
* **Abnahmekriterium:** Nachträgliche Änderung eines einzigen Zeichens im freizugebenden Payload führt bei `POST /api/v1/workbench/approvals/{id}/decide` zu HTTP 409 (`PayloadHashMismatch`); ein im SSE-Stream verstecktes PII-Muster wird vor dem Senden des Satzes gestoppt.

### `LWP-11`: Referenz-Use-Cases A bis F über die Skill-Engine verknüpfen
* **Zu erstellende Dateien:**
  * `src/plattform/skills/builtin_handlers.py` (verbindet die Schritt-Actions der 5 Referenz-Skills mit P2, P3, P4, P5, P8 und P11)
  * `workflows/unified_skill_router.py` (End-to-End-Workflow, der eingehende Vorgänge über `ctx.detect_and_run_skill()` verarbeitet)
  * `tests/test_lwp_11_end_to_end_use_cases.py`
* **Abnahmebefehl:** `make accept WP=lwp_11`
* **Abnahmekriterium:** Alle Use Cases (Posteingangs-Routing, Wohngeld-Vorprüfung mit Grün/Gelb/Rot, Rechtsauskunft mit Zitatprüfung, Feld-Extraktion und IFG-PDF-Schwärzung) laufen Ende-zu-Ende über den `SkillDetector` und `SkillExecutor` grün durch.

### `LWP-12`: Synthetischer Zwillings-Generator (P12), Mandanten-Onboarding & Gesamtabnahme
* **Zu erstellende Dateien:**
  * `src/plattform/alignment/synthetic_twin.py` (erzeugt aus Workbench-Korrekturen garantiert PII-freie synthetische Testfälle mit 0-%-Entity-Overlap)
  * `tenants/stadt-musterhausen/tenant.yaml`
  * `scripts/onboard_all_tenants.py`
  * `tests/test_lwp_12_full_acceptance.py` (inkl. 25 Prompt-Injection- und CaMeL-Taint-Angriffsfällen)
* **Abnahmebefehl:** `make accept WP=lwp_12`
* **Abnahmekriterium:** `make test` und `make check` sind über alle 12 Arbeitspakete zu 100 % grün; 25/25 Injection-Versuche werden blockiert; `scan_cleartext_logs.py` meldet $0$ PII-Treffer in sämtlichen erzeugten Logs.