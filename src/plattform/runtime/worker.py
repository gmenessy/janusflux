"""Hintergrund-Worker & Fair-Queue-Scheduler (`plattform.runtime.worker`, P6, LWP-14).
Eigenschaften:
- Mandanten-faire Job-Verarbeitung (Round-Robin über aktive Mandanten, verhindert Noisy-Neighbor-Blockaden).
- Keine offenen Datenbank-Transaktionen während MediaPipe-Inferenz oder Skill-Schritten
  (Claim -> Inferenz außerhalb DB-Lock -> Status-Update).
- Strukturiertes, garantiert klartext- und PII-freies JSONL-Logging (`logs/worker.jsonl`),
  das zu 100 % kompatibel mit `scripts/scan_cleartext_logs.py` ist.
"""

from __future__ import annotations

import asyncio
import json
import signal
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from workflows.unified_skill_router import (
    ALL_REFERENCE_SKILLS,
    create_default_runtime_context,
    run_unified_skill_workflow,
)


@dataclass
class QueuedSkillJob:
    job_id: str
    tenant_id: str
    schutzklasse: int
    request_text: str
    payload: dict[str, Any]
    explicit_skill_id: str | None = None
    allowed_skills: set[str] = field(default_factory=lambda: set(ALL_REFERENCE_SKILLS))
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "WAITING_HUMAN", "FAILED_SAFE"] = "QUEUED"
    result: dict[str, Any] | None = None


class FairQueueWorker:
    """Mandanten-fairer Hintergrund-Worker für die Ultra-Lightweight-Plattform."""

    def __init__(self, log_path: Path = Path("logs/worker.jsonl")) -> None:
        self.log_path = log_path
        self._tenant_queues: dict[str, deque[QueuedSkillJob]] = {}
        self._tenant_ring: deque[str] = deque()
        self._jobs: dict[str, QueuedSkillJob] = {}
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    def _write_sanitized_log(self, event: str, **metadata: Any) -> None:
        """Schreibt ausschließlich inhalts- und PII-freie Metadaten nach `logs/worker.jsonl`."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **metadata,
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def enqueue(
        self,
        *,
        tenant_id: str,
        request_text: str,
        payload: dict[str, Any],
        schutzklasse: int = 2,
        explicit_skill_id: str | None = None,
        allowed_skills: set[str] | None = None,
    ) -> QueuedSkillJob:
        job = QueuedSkillJob(
            job_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            schutzklasse=schutzklasse,
            request_text=request_text,
            payload=dict(payload),
            explicit_skill_id=explicit_skill_id,
            allowed_skills=allowed_skills or set(ALL_REFERENCE_SKILLS),
        )
        async with self._lock:
            if tenant_id not in self._tenant_queues:
                self._tenant_queues[tenant_id] = deque()
            if tenant_id not in self._tenant_ring:
                self._tenant_ring.append(tenant_id)
            self._tenant_queues[tenant_id].append(job)
            self._jobs[job.job_id] = job

        self._write_sanitized_log(
            "job_enqueued",
            job_id=job.job_id,
            tenant_id=tenant_id,
            schutzklasse=schutzklasse,
        )
        return job

    async def claim_next_fair(self) -> QueuedSkillJob | None:
        """Wählt den nächsten Job per Mandanten-Round-Robin aus (Fair-Queue)."""
        async with self._lock:
            checked = 0
            total_tenants = len(self._tenant_ring)
            while checked < total_tenants and self._tenant_ring:
                tenant_id = self._tenant_ring.popleft()
                q = self._tenant_queues.get(tenant_id)
                if q:
                    job = q.popleft()
                    job.status = "RUNNING"
                    if q:
                        self._tenant_ring.append(tenant_id)
                    return job
                checked += 1
            return None

    async def process_next(self) -> QueuedSkillJob | None:
        """Beansprucht genau einen Job (Phase 1), führt die Inferenz ohne Lock aus
        und aktualisiert den Zielzustand (Phase 2).
        """
        job = await self.claim_next_fair()
        if job is None:
            return None

        try:
            ctx, _, _ = create_default_runtime_context(
                tenant_id=job.tenant_id,
                schutzklasse=job.schutzklasse,
                allowed_skills=job.allowed_skills,
                input_tainted=True,
            )
            outcome = await run_unified_skill_workflow(
                ctx=ctx,
                text=job.request_text,
                payload=job.payload,
                explicit_skill_id=job.explicit_skill_id,
            )
            exec_data = outcome.get("execution")
            if isinstance(exec_data, dict) and exec_data.get("status") == "WAITING_HUMAN":
                job.status = "WAITING_HUMAN"
            else:
                job.status = "COMPLETED"
            job.result = outcome

            detected_skill = outcome.get("detection", {}).get("detected_skill_id")
            routing_action = outcome.get("detection", {}).get("routing_action")
            self._write_sanitized_log(
                "job_processed",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                status=job.status,
                detected_skill_id=detected_skill,
                routing_action=routing_action,
            )
        except (RuntimeError, ValueError, KeyError, PermissionError) as exc:
            job.status = "FAILED_SAFE"
            job.result = {"error_type": type(exc).__name__}
            self._write_sanitized_log(
                "job_failed_safe",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                status=job.status,
                error_type=type(exc).__name__,
            )
        return job

    async def drain_all(self) -> list[QueuedSkillJob]:
        """Verarbeitet alle aktuell in der Fair-Queue wartenden Jobs in Round-Robin-Reihenfolge."""
        processed: list[QueuedSkillJob] = []
        while True:
            job = await self.process_next()
            if job is None:
                break
            processed.append(job)
        return processed

    def request_stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self, poll_interval_s: float = 0.25) -> None:
        self._write_sanitized_log("worker_started")
        while not self._stop_event.is_set():
            job = await self.process_next()
            if job is None:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval_s)
                except TimeoutError:
                    continue
        self._write_sanitized_log("worker_stopped")


async def _main_async() -> None:
    worker = FairQueueWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(_main_async())
