# JanusFlux v2.1.0+mp – Ultra-Lightweight Hybrid-Agentische Verwaltungsplattform

**JanusFlux (`verwaltung-genai-plattform-lightweight`, Version `2.1.0+mp`)** ist die auslieferungsfähige Ultra-Lightweight-Referenzimplementierung der kommunalen GenAI-Verwaltungsplattform auf Basis von **Google MediaPipe (LiteRT)**, einer **deklarativen Vier-Stufen-Skill-Engine** und einem **strukturierten SQLite-FTS5/BM25 LLM-Wiki** mit nativem `[[Wiki-Link]]`-Normgraphen.

---

## 1. Kernarchitektur & Sicherheitsgarantien

1. **Vierstufige Entscheidungskaskade (§ 35a VwVfG / Art. 22 DSGVO / EU AI Act Art. 14):**
   - **Stufe 0 (Deterministisch):** JSON-Schema, Prüfziffern (IBAN Mod-97, Steuer-ID ISO 7064, USt-IdNr., Aktenzeichen), §-31-VwVfG-Fristenrechner, DMN-Entscheidungstabellen (`rules/*.json`) und deterministisches Post-Generation-Verification-Gate (`VAL-006` bis `VAL-009`).
   - **Stufe 1 (Kalibrierte Mikro-Entscheidung):** MediaPipe Decision Port (`contracts/*.yaml`) mit Temperature Scaling ($T = 1.18$) und Conformal Risk Control ($\hat{\lambda}_{\text{auto}} = 0.84, \alpha \le 0.015$).
   - **Stufe 2 (Kontrollierte Synthese):** MediaPipe `LlmInference` (`gemma-3-4b-it-int4.task`) ausschließlich auf pseudonymisierten Daten oder öffentlichen Normen mit wörtlicher Substring-Zitatschranke (`VAL-007`).
   - **Stufe 3 (Menschliche Letztentscheidung / HITL):** BITV-2.0-konforme Verwaltungs-Workbench mit kryptographischer SHA-256-Payload-Hash-Bindung (`409 Conflict` bei nachträglicher Änderung).
2. **Strukturiertes LLM-Wiki (`wiki/normen/*.md`, `wiki/skills/*.md`):**
   - Ersetzt Qdrant, Dense-Embeddings und Cross-Encoder-Reranker durch Markdown-Dokumente mit YAML-Frontmatter, expliziten `[[Wiki-Links]]` (nativer 1-Hop-Normgraph), bi-temporaler Stichtagsfilterung und lokaler SQLite-FTS5/BM25-Volltextsuche inkl. deutscher Kompositazerlegung.
   - **Harte Einzelfallakten-Schranke (`WikiSecurityBoundaryError`):** Dokumente der Schutzklassen $\ge 2$ oder Texte mit personenbezogenen Daten werden deterministisch abgewiesen; Einzelfallakten durchlaufen zwingend PII-Tresor, DMN-Engine und menschliche Freigabe.
3. **Mandanten-Isolation & Revisionssicherheit:**
   - **PostgreSQL 17 `FORCE ROW LEVEL SECURITY`:** Transaktionsgebundene Mandanten-Isolation (`SET LOCAL ROLE app_runtime` + `set_config('app.current_tenant_id', ...)`).
   - **WORM-Audit-Hashkette & Crypto-Shredding (Art. 17 DSGVO):** Verkettete SHA-256-Prüfsummen mit per-Subjekt-Salts (`audit_subject_salts`).
   - **CaMeL Taint-Tracking (F-10):** Unvertrauenswürdige Eingaben (`tainted=True`) können keinen irreversiblen Skill ohne menschliche Freigabe auslösen.

---

## 2. Referenz-Skills (`skills/*/skill.yaml`)

| Skill-ID | Name | Stufen | Wirkung |
|---|---|---|---|
| `dokument.schwaerzen` | Akten- und Dokumentenschwärzung (IFG / RIS) | `[0, 1, 3]` | `reversible_write` |
| `antrag.vorpruefen` | Antrags-Vollständigkeits- & Fristprüfung (§ 31 VwVfG) | `[0, 1, 2, 0]` | `read_only` |
| `dokument.extrahieren` | Strukturierte Dokumenten-Datenextraktion | `[0, 2, 0]` | `read_only` |
| `auskunft.recht` | Gesetzes- & Satzungsauskunft mit Zitatgarantie (LLM-Wiki) | `[0, 2, 0]` | `read_only` |
| `eingang.routen` | Posteingangs-Klassifikation & Zuständigkeits-Routing | `[0, 1, 0]` | `reversible_write` |

---

## 3. Schnellstart & CLI (`janusflux` / `plattform-cli`)

```bash
# 1. Statische Qualitäts-, Typ-, Architektur- und Log-Gates prüfen
make check

# 2. Gesamte Abnahme-Testsuite (LWP-01 bis LWP-18 inkl. Live-PostgreSQL-RLS) ausführen
make test

# 3. Goldset (200 Fälle) & Kalibrierungsbericht (eval/berichte/gemma-4b-mediapipe-de.md) erzeugen
uv run --no-sync python -m plattform.cli evaluate

# 4. Auslieferungs-Manifest (RELEASE_MANIFEST.json) verifizieren
uv run --no-sync python -m plattform.cli release-check

# 5. FastAPI-Server & BITV-2.0-Web-Workbench starten
uv run --no-sync python -m plattform.cli serve --host 0.0.0.0 --port 8000
```

---

## 4. REST-API & BITV-2.0-Workbench (`src/plattform/main.py`)

- `GET /` & `GET /workbench`: Barrierefreie BITV-2.0-Verwaltungs-Workbench (HTML5, Skip-Links, ARIA-Landmarks, Live-Skill-Dispatch)
- `GET /healthz`: Plattform-Systemstatus
- `GET /api/v1/skills`: Deklarativer Skill-Katalog
- `POST /api/v1/skills/detect`: Vierstufiger Skill-Detektor
- `POST /api/v1/skills/dispatch`: End-to-End-Ausführung über `RunContext` inkl. CaMeL-Taint-Tracking & WORM-Audit
- `GET /api/v1/wiki/nodes` & `GET /api/v1/wiki/search`: LLM-Wiki-Knoten & bi-temporale 1-Hop-Normsuche
- `POST /api/v1/edge/stream`: 64-Zeichen-Lookahead-SSE-Proxy mit Echtzeit-PII-Schranke
- `GET /api/v1/audit/{tenant_id}/verify` & `POST /api/v1/audit/{tenant_id}/crypto-shred`: WORM-Audit-Kettenprüfung & DSGVO-Crypto-Shredding
- `POST /api/v1/documents/render-pdf-ua` & `POST /api/v1/documents/redact-pdf`: PDF/UA-1-Bescheid-Generator & echte physische PyMuPDF-Schwärzung
- `POST /api/v1/alignment/synthetic-twin`: Synthetischer Zwillings-Generator mit 0-%-Entity-Overlap-Garantie
