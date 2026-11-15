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
