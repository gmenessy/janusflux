"""Abnahmetests für LWP-14: Hintergrund-Worker (`FairQueueWorker`), Sanitized JSONL-Logs
und 4-Container-Deployment-Artefakte (`Dockerfile.lightweight`, `docker-compose.lightweight.yaml`, OPA-Rego).
"""

from pathlib import Path

import yaml

from plattform.runtime.worker import FairQueueWorker
from scripts.scan_cleartext_logs import scan_logs_directory


async def test_lwp_14_fair_queue_worker_round_robin_and_clean_logs(tmp_path: Path) -> None:
    log_file = tmp_path / "worker.jsonl"
    worker = FairQueueWorker(log_path=log_file)

    # Stadt A stellt 3 Jobs am Stück ein, Stadt B stellt 2 Jobs ein
    for i in range(3):
        await worker.enqueue(
            tenant_id="stadt-a",
            request_text=f"Posteingang #{i} klassifizieren",
            payload={
                "eingang_text": (
                    "Antrag von Herrn Max Mustermann (IBAN DE89370400440532013000) auf Wohngeld."
                )
            },
        )
    for j in range(2):
        await worker.enqueue(
            tenant_id="stadt-b",
            request_text=f"Rechtsauskunft #{j} zu § 31 VwVfG",
            payload={"fragestellung": "Wie wird eine Frist nach § 31 VwVfG berechnet?"},
        )

    processed = await worker.drain_all()
    assert len(processed) == 5

    # Round-Robin-Fairness: Stadt B wartet NICHT hinter allen 3 Jobs von Stadt A!
    tenant_order = [job.tenant_id for job in processed]
    assert tenant_order == ["stadt-a", "stadt-b", "stadt-a", "stadt-b", "stadt-a"]
    assert all(job.status == "COMPLETED" for job in processed)

    # Log-Scanner muss auf den erzeugten Worker-Logs 0 PII-/Klartext-Verstöße melden
    assert log_file.exists()
    violations = scan_logs_directory(tmp_path)
    assert violations == []


def test_lwp_14_deployment_artifacts_valid() -> None:
    dockerfile = Path("deploy/compose/Dockerfile.lightweight")
    compose_file = Path("deploy/compose/docker-compose.lightweight.yaml")
    opa_rego = Path("deploy/opa/plattform_authz.rego")

    assert dockerfile.exists()
    assert "USER plattform" in dockerfile.read_text(encoding="utf-8")

    assert opa_rego.exists()
    assert "package plattform.authz" in opa_rego.read_text(encoding="utf-8")

    compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    services = set(compose_data["services"].keys())
    # Exakt 4 Container (Qdrant ist durch das SQLite-FTS5 LLM-Wiki entfallen)
    assert services == {"postgres", "opa", "platform-api", "platform-worker"}
