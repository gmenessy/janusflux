"""Tests für LWP-18: CLI (`janusflux`), CI/CD-Workflow, Release-Manifest & Gesamtauslieferungsreife."""

from __future__ import annotations

import json
from pathlib import Path

from plattform.cli import REQUIRED_RELEASE_ARTIFACTS, build_release_manifest, run_cli


def test_lwp_18_cli_commands_and_release_manifest() -> None:
    # 1. CLI 'check' (Architektur + Log-Scan)
    assert run_cli(["check"]) == 0

    # 2. CLI 'onboard' (Mandanten-Onboarding)
    assert run_cli(["onboard"]) == 0

    # 3. CLI 'release-check' & Manifest-Prüfung
    assert run_cli(["release-check"]) == 0

    manifest_path = Path("RELEASE_MANIFEST.json")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "2.1.0+mp"
    assert manifest["status"] == "DELIVERY_READY"
    assert len(manifest["components"]["skills_registered"]) == 5
    assert manifest["components"]["wiki_nodes_count"] >= 8
    assert len(manifest["artifacts_sha256"]) == len(REQUIRED_RELEASE_ARTIFACTS)

    for rel, digest in manifest["artifacts_sha256"].items():
        assert Path(rel).exists()
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64


def test_lwp_18_ci_workflow_and_documentation_complete() -> None:
    ci_path = Path(".github/workflows/ci.yml")
    assert ci_path.exists()
    ci_text = ci_path.read_text(encoding="utf-8")
    assert "check_architecture.py" in ci_text
    assert "scan_cleartext_logs.py" in ci_text
    assert "pytest tests/" in ci_text
    assert "release-check" in ci_text

    readme_path = Path("README.md")
    assert readme_path.exists()
    readme_text = readme_path.read_text(encoding="utf-8")
    assert "2.1.0+mp" in readme_text
    assert "LLM-Wiki" in readme_text
    assert "FORCE ROW LEVEL SECURITY" in readme_text

    direct_manifest = build_release_manifest(Path("."))
    assert direct_manifest["status"] == "DELIVERY_READY"
