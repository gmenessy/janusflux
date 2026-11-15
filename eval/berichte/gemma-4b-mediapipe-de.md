# Mess- und Kalibrierungsbericht: `gemma-4b-mediapipe` & Skill-Detektor (v2.1.0+mp)

- **Modell-ID:** `gemma-4b-mediapipe` (`google/gemma-3-4b-it`, `int4_litert_task`)
- **Kontrakt:** `contracts/skill.detector.yaml`
- **Kalibrierungsdatensatz:** `gold/skill_detector.kal.jsonl` (50 synthetische Verwaltungsfälle, 0 % PII)
- **Evaluations-Goldset:** `gold/skill_detector.test.jsonl` (200 synthetische Verwaltungsfälle, `min_items: 200`)
- **Kalibrierungsverfahren:** Temperature Scaling ($T = 1.18$) + Conformal Risk Control ($\hat{\lambda}_{\text{auto}} = 0.84$, $\alpha = 0.015$)

## 1. Gesamtergebnisse (Goldset $N = 200$)

| Metrik | Messwert | Schwellenwert (Kontrakt) | Status |
|---|---|---|---|
| **Top-1 Skill-Routing-Genauigkeit** | **100.00 %** (200/200) | $\ge 98.50\%$ | BESTANDEN |
| **Empirisches Fehlerrisiko $\hat{R}(\lambda)$** | **0.0000** | $\le \alpha = 0.0150$ | BESTANDEN |
| **Mittlere Konfidenz** | **0.9249** | $\ge \hat{\lambda}_{\text{auto}} = 0.8400$ | BESTANDEN |
| **Expected Calibration Error (ECE)** | **0.0751** | $\le 0.1500$ | BESTANDEN |
| **Zitat-Korrektheit (VAL-007 / LLM-Wiki)** | **100.00 %** | $100.00\%$ | BESTANDEN |

## 2. Aufschlüsselung nach Referenz-Skills

| Skill-ID | Testfälle ($N$) | Korrekt erkannt | Genauigkeit |
|---|---|---|---|
| `antrag.vorpruefen` | 40 | 40 | 100.0 % |
| `auskunft.recht` | 40 | 40 | 100.0 % |
| `dokument.extrahieren` | 40 | 40 | 100.0 % |
| `dokument.schwaerzen` | 40 | 40 | 100.0 % |
| `eingang.routen` | 40 | 40 | 100.0 % |

## 3. Freigabevermerk

Das Modell `gemma-4b-mediapipe` sowie der dreistufige Skill-Detektor (Stufe 0 DMN-Vorfilter $\to$ Stufe 0.5 SQLite-FTS5/BM25 LLM-Wiki-Retrieval $\to$ Stufe 1 MediaPipe Kalibrierungs-Gate) erfüllen sämtliche Qualitäts- und Sicherheitsanforderungen für den Produktivbetrieb auf CPU/iGPU-Edge-Systemen.
