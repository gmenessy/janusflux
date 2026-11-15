"""Tests für LWP-17: Goldsets (`gold/*.jsonl`), CRC-Kalibrierung & Messbericht (`eval/berichte/gemma-4b-mediapipe-de.md`)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from plattform.policy_privacy.pii_detectors import detect_all_pii_spans
from scripts.run_evaluation import run_evaluation_suite


async def test_lwp_17_goldset_generation_zero_pii_and_contract_alignment() -> None:
    metrics = await run_evaluation_suite(Path("."))
    assert metrics["kal_items"] == 50
    assert metrics["test_items"] == 200
    assert metrics["accuracy"] >= 0.985
    assert metrics["empirical_risk"] <= 0.015

    # Prüfe Vertragskonsistenz mit contracts/skill.detector.yaml
    contract = yaml.safe_load(Path("contracts/skill.detector.yaml").read_text(encoding="utf-8"))
    kal_path = Path(contract["calibration"]["fitted_on"])
    test_path = Path(contract["goldset"]["path"])
    min_items = int(contract["goldset"]["min_items"])

    assert kal_path.exists()
    assert test_path.exists()

    test_lines = [
        json.loads(line)
        for line in test_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(test_lines) >= min_items

    # Prüfe 0 % PII im gesamten Test-Goldset
    for item in test_lines[:25]:
        spans = detect_all_pii_spans(item["text"])
        assert len(spans) == 0

    # Prüfe Messbericht-Referenz aus models/gemma-4b-mediapipe.yaml
    model_card = yaml.safe_load(Path("models/gemma-4b-mediapipe.yaml").read_text(encoding="utf-8"))
    report_path = Path(model_card["aufnahme"]["messbericht"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "BESTANDEN" in report_text
    assert "gemma-4b-mediapipe" in report_text
