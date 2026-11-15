"""Kommandozeilen-Werkzeug (`janusflux` / `plattform-cli`, LWP-18).
Unterstützt:
- `check`: Führt Architektur-Linter und Klartext-/PII-Log-Scanner aus
- `onboard`: Führt das deklarative Mandanten-Onboarding aus (`tenants/*/tenant.yaml`)
- `evaluate`: Führt Goldset-Generierung, CRC-Kalibrierung und Messbericht-Erstellung aus
- `release-check`: Prüft sämtliche Auslieferungsartefakte (SQL, DMN, Modelle, Kontrakte, Skills,
  LLM-Wiki, Goldset, Messbericht, Docker/OPA) und schreibt `RELEASE_MANIFEST.json`
- `serve`: Startet den FastAPI-Server via Uvicorn
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import uvicorn

from plattform.decision.dmn import DMNEngine
from plattform.knowledge.llm_wiki import LLMWikiEngine
from plattform.model_gateway.mediapipe_adapter import MediaPipeGatewayAdapter
from plattform.skills.registry import SkillRegistry

REQUIRED_RELEASE_ARTIFACTS: tuple[str, ...] = (
    "pyproject.toml",
    "Makefile",
    "CLAUDE.md",
    "db/migrations/001_core_platform.sql",
    "db/migrations/002_skills_and_lightweight.sql",
    "rules/skill_routing.json",
    "rules/antrag_vollstaendigkeit.json",
    "rules/eingang_routing.json",
    "models/gemma-4b-mediapipe.yaml",
    "models/mediapipe-embedder.yaml",
    "models/mediapipe-classifier.yaml",
    "contracts/skill.detector.yaml",
    "contracts/eingang.gate.yaml",
    "contracts/antrag.nachweise.yaml",
    "contracts/schwaerzung.pruefung.yaml",
    "skills/dokument.schwaerzen/skill.yaml",
    "skills/antrag.vorpruefen/skill.yaml",
    "skills/dokument.extrahieren/skill.yaml",
    "skills/auskunft.recht/skill.yaml",
    "skills/eingang.routen/skill.yaml",
    "tenants/stadt-musterhausen/tenant.yaml",
    "deploy/compose/docker-compose.lightweight.yaml",
    "deploy/compose/Dockerfile.lightweight",
    "deploy/opa/plattform_authz.rego",
    "gold/skill_detector.kal.jsonl",
    "gold/skill_detector.test.jsonl",
    "eval/berichte/gemma-4b-mediapipe-de.md",
    "docs/ONBOARDING_UND_NUTZUNGSHANDBUCH.md",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def build_release_manifest(base_dir: Path = Path(".")) -> dict[str, Any]:
    missing: list[str] = []
    checksums: dict[str, str] = {}

    for rel in REQUIRED_RELEASE_ARTIFACTS:
        p = base_dir / rel
        if not p.exists():
            missing.append(rel)
        else:
            checksums[rel] = _sha256_file(p)

    if missing:
        raise FileNotFoundError(f"Fehlende Pflicht-Auslieferungsartefakte: {missing}")

    gateway = MediaPipeGatewayAdapter()
    registry = SkillRegistry(base_dir / "skills", wiki_dir=base_dir / "wiki")
    registry.load_all(embedder_fn=gateway.embed_sync)
    wiki = LLMWikiEngine(base_dir / "wiki")
    dmn = DMNEngine(base_dir / "rules")

    manifest: dict[str, Any] = {
        "product": "JanusFlux – MediaPipe Ultra-Lightweight Skill & LLM-Wiki Platform",
        "version": "2.1.0+mp",
        "status": "DELIVERY_READY",
        "components": {
            "skills_registered": [s.skill_id for s in registry.list_all()],
            "wiki_nodes_count": len(wiki._nodes),
            "dmn_tables": sorted(dmn._tables.keys()),
        },
        "artifacts_sha256": checksums,
    }
    manifest_path = base_dir / "RELEASE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="janusflux",
        description="JanusFlux v2.1.0+mp – Ultra-Lightweight Verwaltungsplattform CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Prüft Architektur-Injektionsregeln und Log-Hygiene")
    sub.add_parser("onboard", help="Führt das deklarative Mandanten-Onboarding aus")
    sub.add_parser("evaluate", help="Führt Goldset-Generierung & CRC-Evaluation aus")
    sub.add_parser(
        "release-check",
        help="Prüft sämtliche Auslieferungsartefakte und erzeugt RELEASE_MANIFEST.json",
    )
    serve_p = sub.add_parser("serve", help="Startet den FastAPI-Server")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.command == "check":
        from scripts.check_architecture import main as arch_main
        from scripts.scan_cleartext_logs import main as log_main

        rc1 = arch_main()
        rc2 = log_main()
        return 0 if (rc1 == 0 and rc2 == 0) else 1

    if args.command == "onboard":
        from scripts.onboard_all_tenants import onboard_all_tenants

        res = asyncio.run(onboard_all_tenants(Path("tenants")))
        print(f"[ONBOARD-OK] {len(res)} Mandant(en) eingerichtet.")
        return 0

    if args.command == "evaluate":
        from scripts.run_evaluation import run_evaluation_suite

        metrics = asyncio.run(run_evaluation_suite(Path(".")))
        print(
            f"[EVAL-OK] Goldset ({metrics['test_items']} Fälle): "
            f"Accuracy={metrics['accuracy'] * 100:.1f}%"
        )
        return 0

    if args.command == "release-check":
        manifest = build_release_manifest(Path("."))
        print(
            f"[RELEASE-OK] {manifest['product']} ({manifest['version']}): "
            f"{len(manifest['artifacts_sha256'])} Pflichtartefakte verifiziert -> RELEASE_MANIFEST.json"
        )
        return 0

    if args.command == "serve":
        uvicorn.run("plattform.main:app", host=args.host, port=args.port)
        return 0

    return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
