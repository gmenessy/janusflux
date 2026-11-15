"""Abnahmetests für LWP-04: MediaPipe Model Gateway Adapter (P5) & Modellsteckbrief-Prüfung."""

from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import BaseModel

from plattform.model_gateway.mediapipe_adapter import (
    MediaPipeGatewayAdapter,
    ModelIntegrityError,
)
from plattform.model_gateway.quota import QuotaExceededError, TwoPhaseTokenQuotaManager


class _FlatExtractionOut(BaseModel):
    sachverhalt_zusammenfassung: str
    zitat_wortlaut: str


def test_lwp_04_model_cards_exist_and_sha256_mismatch_raises(tmp_path: Path) -> None:
    for card in [
        "models/gemma-4b-mediapipe.yaml",
        "models/mediapipe-embedder.yaml",
        "models/mediapipe-classifier.yaml",
    ]:
        data = yaml.safe_load(Path(card).read_text(encoding="utf-8"))
        assert "sha256_manifest" in data

    fake_model = tmp_path / "gemma-3-4b-it-int4.task"
    fake_model.write_bytes(b"corrupted-model-bytes")

    adapter = MediaPipeGatewayAdapter(
        llm_model_path=fake_model,
        embedder_model_path=tmp_path / "missing.tflite",
        expected_hashes={"gemma-3-4b-it-int4.task": "sha256:0000000000000000"},
    )
    with pytest.raises(ModelIntegrityError):
        adapter.verify_and_initialize()


async def test_lwp_04_embedding_returns_l2_normalized_vectors() -> None:
    adapter = MediaPipeGatewayAdapter()
    vec = await adapter.embed_single("Antrag auf Wohngeld nach dem Wohngeldgesetz")
    norm = float(np.linalg.norm(vec))
    assert abs(norm - 1.0) < 1e-5


async def test_lwp_04_decision_port_crc_threshold_routes_uncertain_to_human() -> None:
    adapter = MediaPipeGatewayAdapter()
    # Zwei fast identische Optionen -> Konfidenz ~ 0.50 < crc_lambda (0.85) -> zwingend path = 'human'
    res = await adapter.evaluate_decision_contract(
        contract_id="skill.detector",
        state_text="Allgemeine Anfrage zur Verwaltung",
        dynamic_options={"ziel_skill": ["option_a", "option_a"]},
        crc_lambda=0.85,
    )
    assert res["questions"]["ziel_skill"]["path"] == "human"
    assert res["overall_path"] == "human"


async def test_lwp_04_llm_structured_output_one_repair_and_val006_rejection() -> None:
    calls: list[str] = []

    def repair_succeeds_sim(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "```json\n{\"sachverhalt_zusammenfassung\": \"Nur ein Feld\"}\n```"
        return '{"sachverhalt_zusammenfassung": "Korrigiert", "zitat_wortlaut": "§ 31 VwVfG"}'

    adapter_ok = MediaPipeGatewayAdapter(llm_simulator_fn=repair_succeeds_sim)
    parsed = await adapter_ok.generate_structured(
        system_prompt="Extrahiere Felder.",
        user_prompt="Bitte § 31 VwVfG prüfen.",
        output_model=_FlatExtractionOut,
        max_repair_attempts=1,
    )
    assert parsed.sachverhalt_zusammenfassung == "Korrigiert"
    assert len(calls) == 2

    adapter_fail = MediaPipeGatewayAdapter(llm_simulator_fn=lambda _: '{"broken": true}')
    with pytest.raises(ValueError, match="VAL-006"):
        await adapter_fail.generate_structured(
            system_prompt="Extrahiere Felder.",
            user_prompt="Test",
            output_model=_FlatExtractionOut,
            max_repair_attempts=1,
        )


async def test_lwp_04_two_phase_token_quota_preauthorize_and_settle() -> None:
    quota = TwoPhaseTokenQuotaManager(default_daily_budget=1000)
    res1 = await quota.pre_authorize("stadt-a", 600)
    with pytest.raises(QuotaExceededError):
        await quota.pre_authorize("stadt-a", 500)

    total_used = await quota.settle(res1.reservation_id, 400)
    assert total_used == 400
    usage = await quota.get_usage("stadt-a")
    assert usage["available"] == 600
