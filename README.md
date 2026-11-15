# JanusFlux – Ultra-Lightweight Hybrid GenAI Platform (MediaPipe & SQLite-FTS5 LLM-Wiki)

**JanusFlux** ist eine Ultra-Lightweight-Implementierung einer hybrid-agentischen Verwaltungsplattform auf Basis von **Google MediaPipe (LiteRT)**, einer **4-stufigen Skill-Detection- & Execution-Engine** sowie einem **strukturierten Markdown-LLM-Wiki mit lokaler SQLite-FTS5/BM25-Volltextsuche**.

## Architektur-Highlights

1. **Vier-Stufen-Kaskade:**
   - **Stufe 0 (Deterministisch, `< 20 ms`):** DMN-Entscheidungstabellen (`rules/*.json`), Fristberechnung nach § 31 VwVfG ([`fristen.py`](src/plattform/decision/fristen.py)), Prüfziffern (IBAN Mod 97, Steuer-ID ISO 7064), Zwei-Fassungen-PII-Tresor (`AES-256-GCM`, [`redaction_vault.py`](src/plattform/policy_privacy/redaction_vault.py)), Post-Execution-Gate `VAL-001`–`VAL-010` ([`post_gate.py`](src/plattform/decision/post_gate.py)) und physische PDF-Schwärzung mit 0-%-Resttext-Beweis ([`physical_redactor.py`](src/plattform/document_output/physical_redactor.py)).
   - **Stufe 0.5 & P4 (Strukturiertes LLM-Wiki auf SQLite FTS5 / BM25):** Dateibasiertes Markdown-Wiki (`wiki/normen/*.md`, `wiki/skills/*.md`) mit YAML-Frontmatter, nativen `[[Wiki-Links]]` für den 1-Hop-Normgraphen, bi-temporaler Stichtagsfilterung und deutscher Kompositazerlegung ([`llm_wiki.py`](src/plattform/knowledge/llm_wiki.py)) – ersetzt `Qdrant` und schwere Embedding-Server komplett.
   - **Stufe 1 (Decision Port über MediaPipe):** Kalibrierte Klassifikation mit Temperatur-Skalierung und Conformal Risk Control ($\hat{\lambda}_{\text{auto}}$) über [`MediaPipeGatewayAdapter`](src/plattform/model_gateway/mediapipe_adapter.py).
   - **Stufe 2 (Generatives LLM über MediaPipe):** Lokale `Gemma 4B INT4`-Inferenz mit flachem Pydantic-JSON-Schema und 1x Auto-Reparatur (`VAL-006`).
   - **Stufe 3 (Mensch / BITV-2.0-Workbench):** Serverseitige Split-Screen-Freigabe (`L1`–`L3`) mit kryptografischer `SHA-256`-Payload-Hash-Bindung ([`routes.py`](src/plattform/workbench/routes.py)).

2. **Harte Grenze bei Einzelfallakten:**
   - Das LLM-Wiki akzeptiert ausschließlich Rechtsnormen, Satzungen und Skill-Knoten der Schutzklassen `0` und `1` (`WikiSecurityBoundaryError` bei `schutzklasse >= 2` oder PII).
   - Für Einzelfallakten bleiben PII-Tresor, DMN-Engine, §-31-VwVfG-Fristen und menschliche Freigabe zwingend vor- bzw. nachgeschaltet.

3. **4-Container-Topologie ([`docker-compose.lightweight.yaml`](deploy/compose/docker-compose.lightweight.yaml)):**
   - `postgres` (PostgreSQL 17 mit `FORCE ROW LEVEL SECURITY` und Audit-Hash-Kette inkl. Crypto-Shredding)
   - `opa` (Open Policy Agent ABAC-Sidecar)
   - `platform-api` (FastAPI Edge-, Skill- & Workbench-Server)
   - `platform-worker` (Mandanten-fairer Round-Robin-Hintergrund-Worker)

## Schnellstart & Abnahme

```bash
# Statische Architektur-, Ruff-, Mypy- und PII-Log-Prüfung
make check

# Gesamte Test-Suite (LWP-01 bis LWP-14) ausführen
make test

# Einzelnes Arbeitspaket abnehmen (z. B. LWP-13 LLM-Wiki)
make accept WP=lwp_13

# 4-Container-Stack per Docker Compose starten
make up
```
