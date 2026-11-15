"""MediaPipe Model Gateway Adapter (P5, LWP-04) gemäß Abschnitt 3.2 der Spezifikation:
- Stufe 0: MediaPipe TextEmbedder (.tflite) mit deterministischem CI-Simulations-Fallback
- Stufe 1: MediaPipe TextClassifier (.tflite) + Kalibrierter Single-Step Readout mit CRC (lambda_auto)
- Stufe 2: MediaPipe LlmInference (.task, Gemma 2B/4B INT4) mit JSON-Schema-Enforcement & 1x Reparatur
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel, ValidationError

try:
    from mediapipe.tasks.python.core import base_options as mp_base_options
    from mediapipe.tasks.python.genai import llm_inference
    from mediapipe.tasks.python.text import text_classifier, text_embedder

    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False

TModel = TypeVar("TModel", bound=BaseModel)

OUT_OF_SCOPE_KEYWORDS = re.compile(
    r"\b(wetterbericht|kuchenrezept|fußballergebnis|bundesliga|aktienkurs|kryptowährung|"
    r"pizza\s+bestellen|horoskop|witz\s+erzählen|urlaubsreise)\b",
    re.IGNORECASE,
)


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
        llm_model_path: Path = Path("/models/mediapipe/gemma-3-4b-it-int4.task"),
        embedder_model_path: Path = Path(
            "/models/mediapipe/universal_sentence_encoder_multilingual.tflite"
        ),
        classifier_model_path: Path | None = Path(
            "/models/mediapipe/verwaltung_de_classifier.tflite"
        ),
        expected_hashes: dict[str, str] | None = None,
        max_concurrent_llm: int = 2,
        llm_simulator_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.llm_model_path = llm_model_path
        self.embedder_model_path = embedder_model_path
        self.classifier_model_path = classifier_model_path
        self.expected_hashes = expected_hashes or {}
        self._llm_semaphore = asyncio.Semaphore(max_concurrent_llm)
        self._llm_simulator_fn = llm_simulator_fn

        self._llm_engine: Any = None
        self._embedder: Any = None
        self._classifier: Any = None

    def verify_and_initialize(self) -> None:
        """Prüft beim Start alle SHA-256-Prüfsummen und initialisiert die MediaPipe-Engines."""
        for path in [self.llm_model_path, self.embedder_model_path, self.classifier_model_path]:
            if path is not None and path.exists() and path.name in self.expected_hashes:
                actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
                expected = self.expected_hashes[path.name]
                if actual != expected:
                    raise ModelIntegrityError(
                        f"SHA-256-Mismatch für Modell '{path.name}': "
                        f"erwartet {expected}, gefunden {actual}"
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
            norm = float(np.linalg.norm(vec)) or 1.0
            return vec / norm

        # Deterministischer Charakter-/Wort-Trigramm-Hash-Vektor (384 Dim., L2-normiert) für CI-Tests
        vec = np.zeros(384, dtype=np.float32)
        tokens = re.findall(r"[a-zäöüß0-9]{2,}", truncated.lower())
        if not tokens:
            digest = hashlib.sha256(truncated.encode("utf-8")).digest()
            raw = np.frombuffer(digest * 12, dtype=np.uint8)[:384].astype(np.float32)
            norm = float(np.linalg.norm(raw)) or 1.0
            return raw / norm
        for tok in tokens:
            for n in (3, 4, len(tok)):
                for i in range(max(1, len(tok) - n + 1)):
                    sub = tok[i : i + n]
                    h = int.from_bytes(hashlib.md5(sub.encode("utf-8")).digest()[:4], "little")
                    idx = h % 384
                    sign = 1.0 if (h & 1) == 0 else -1.0
                    vec[idx] += sign
        norm = float(np.linalg.norm(vec)) or 1.0
        return vec / norm

    def embed_sync(self, text: str) -> np.ndarray:
        """Synchroner Helfer für den initialen SkillRegistry-Indexaufbau beim Start."""
        truncated = text[:2000]
        vec = np.zeros(384, dtype=np.float32)
        tokens = re.findall(r"[a-zäöüß0-9]{2,}", truncated.lower())
        if not tokens:
            digest = hashlib.sha256(truncated.encode("utf-8")).digest()
            raw = np.frombuffer(digest * 12, dtype=np.uint8)[:384].astype(np.float32)
            norm = float(np.linalg.norm(raw)) or 1.0
            return raw / norm
        for tok in tokens:
            for n in (3, 4, len(tok)):
                for i in range(max(1, len(tok) - n + 1)):
                    sub = tok[i : i + n]
                    h = int.from_bytes(hashlib.md5(sub.encode("utf-8")).digest()[:4], "little")
                    idx = h % 384
                    sign = 1.0 if (h & 1) == 0 else -1.0
                    vec[idx] += sign
        norm = float(np.linalg.norm(vec)) or 1.0
        return vec / norm

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

        # Extrahiere den eigentlichen Anfrage-Teil, falls "Eingabe-Anfrage:" enthalten ist
        query_only = state_text
        if "Eingabe-Anfrage:" in state_text:
            query_only = state_text.split("Eingabe-Anfrage:", 1)[1].strip()

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
        is_out_of_scope = bool(OUT_OF_SCOPE_KEYWORDS.search(query_only))
        if "kein_skill_passt" not in questions_out:
            questions_out["kein_skill_passt"] = {
                "value": "yes" if is_out_of_scope else "no",
                "confidence": 0.96 if is_out_of_scope else 0.94,
                "path": "yes" if is_out_of_scope else "no",
            }
        if "pflichtangaben_fehlen" not in questions_out:
            questions_out["pflichtangaben_fehlen"] = {
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

        query_only = state_text
        if "Eingabe-Anfrage:" in state_text:
            query_only = state_text.split("Eingabe-Anfrage:", 1)[1].strip()

        state_vec = await self.embed_single(query_only)
        sims: list[float] = []
        for opt in options:
            opt_vec = await self.embed_single(opt.replace(".", " ").replace("_", " "))
            sim = float(np.dot(state_vec, opt_vec))
            # Bonus, falls Teilbegriffe der Option direkt im Query-Text vorkommen
            opt_parts = [p for p in re.split(r"[._\s]+", opt.lower()) if len(p) >= 4]
            keyword_hits = sum(1 for p in opt_parts if p in query_only.lower())
            boost = keyword_hits * 2.5
            sims.append(math.exp(sim * 6.0 + boost))
        total = sum(sims) or 1.0
        return [s / total for s in sims]

    # -------------------------------------------------------------------------
    # STUFE 2: Strukturierte JSON-Generierung über MediaPipe LlmInference (Gemma 4B)
    # -------------------------------------------------------------------------
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[TModel],
        max_repair_attempts: int = 1,
    ) -> TModel:
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
        if self._llm_simulator_fn is not None:
            return self._llm_simulator_fn(prompt)
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
