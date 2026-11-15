"""Unveränderliche Audit-Hash-Kette mit Crypto-Shredding nach Art. 17 DSGVO (LWP-02).
- Serialisierter Sequencer pro Mandant (`seq_no = 1..N` lückenlos).
- Pseudonymisierung von Betroffenen-Identifikatoren via `HMAC_SHA256(salt_bytes, subject_ref)`.
- Nach Löschen des Salts in `audit_subject_salts` (Crypto-Shredding) ist der Personenbezug
  unwiederbringlich aufgehoben, während die kryptografische Hash-Kette zu 100 % valide bleibt.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from plattform.db import RLSDatabasePool


@dataclass(frozen=True)
class AuditEventRecord:
    tenant_id: str
    seq_no: int
    run_id: str
    step_id: str
    stufe: int
    schutzklasse: int
    path_taken: str
    subject_pseudonym: str | None
    prev_hash: str
    entry_hash: str


class AuditChainIntegrityError(RuntimeError):
    """Wird ausgelöst, wenn eine Manipulation oder Lücke in der Audit-Hash-Kette erkannt wird."""


class AuditWriter:
    """Mandanten-isolierter Audit-Sequencer mit HMAC-Pseudonymisierung und Ketten-Verifizierer."""

    def __init__(self, db_pool: RLSDatabasePool | None = None) -> None:
        self._db = db_pool or RLSDatabasePool()
        self._tenant_locks: dict[str, asyncio.Lock] = {}
        self._heads: dict[str, tuple[int, str]] = {}

    def _get_tenant_lock(self, tenant_id: str) -> asyncio.Lock:
        if tenant_id not in self._tenant_locks:
            self._tenant_locks[tenant_id] = asyncio.Lock()
        return self._tenant_locks[tenant_id]

    @staticmethod
    def compute_entry_hash(
        *,
        tenant_id: str,
        seq_no: int,
        run_id: str,
        step_id: str,
        stufe: int,
        schutzklasse: int,
        path_taken: str,
        subject_pseudonym: str | None,
        prev_hash: str,
    ) -> str:
        canonical = (
            f"{tenant_id}|{seq_no}|{run_id}|{step_id}|{stufe}|"
            f"{schutzklasse}|{path_taken}|{subject_pseudonym or ''}|{prev_hash}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def get_or_create_subject_pseudonym(self, tenant_id: str, subject_ref: str) -> str:
        """Erzeugt oder lädt das mandantenspezifische Salt und berechnet HMAC-SHA256."""
        async with self._db.tenant_session(tenant_id) as session:
            existing = await session.select("audit_subject_salts", subject_ref=subject_ref)
            if existing:
                salt_hex: str = existing[0]["salt_hex"]
                salt_bytes = bytes.fromhex(salt_hex)
            else:
                salt_bytes = secrets.token_bytes(32)
                await session.insert(
                    "audit_subject_salts",
                    {
                        "tenant_id": tenant_id,
                        "subject_ref": subject_ref,
                        "salt_hex": salt_bytes.hex(),
                    },
                )
        digest = hmac.new(salt_bytes, subject_ref.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"subj_hmac:{digest}"

    async def crypto_shred_subject(self, tenant_id: str, subject_ref: str) -> int:
        """Art. 17 DSGVO Crypto-Shredding: Löscht das Salt des Betroffenen unwiderruflich."""
        async with self._db.tenant_session(tenant_id) as session:
            return await session.delete("audit_subject_salts", subject_ref=subject_ref)

    async def record_step(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        stufe: int,
        schutzklasse: int,
        path_taken: str = "auto",
        subject_ref: str | None = None,
    ) -> AuditEventRecord:
        """Schreibt einen inhaltsfreien Audit-Eintrag serialisiert in die Hash-Kette des Mandanten."""
        pseudonym: str | None = None
        if subject_ref is not None:
            pseudonym = await self.get_or_create_subject_pseudonym(tenant_id, subject_ref)

        lock = self._get_tenant_lock(tenant_id)
        async with lock:
            last_seq, prev_hash = self._heads.get(tenant_id, (0, "GENESIS"))
            seq_no = last_seq + 1
            entry_hash = self.compute_entry_hash(
                tenant_id=tenant_id,
                seq_no=seq_no,
                run_id=run_id,
                step_id=step_id,
                stufe=stufe,
                schutzklasse=schutzklasse,
                path_taken=path_taken,
                subject_pseudonym=pseudonym,
                prev_hash=prev_hash,
            )
            self._heads[tenant_id] = (seq_no, entry_hash)

            record_dict: dict[str, Any] = {
                "tenant_id": tenant_id,
                "seq_no": seq_no,
                "run_id": run_id,
                "step_id": step_id,
                "stufe": stufe,
                "schutzklasse": schutzklasse,
                "path_taken": path_taken,
                "subject_pseudonym": pseudonym,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
            }
            async with self._db.tenant_session(tenant_id) as session:
                await session.insert("audit_events", record_dict)

            return AuditEventRecord(**record_dict)

    async def list_events(self, tenant_id: str) -> list[AuditEventRecord]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = await session.select("audit_events")
        rows.sort(key=lambda r: int(r["seq_no"]))
        return [
            AuditEventRecord(
                tenant_id=r["tenant_id"],
                seq_no=int(r["seq_no"]),
                run_id=r["run_id"],
                step_id=r["step_id"],
                stufe=int(r["stufe"]),
                schutzklasse=int(r["schutzklasse"]),
                path_taken=r["path_taken"],
                subject_pseudonym=r.get("subject_pseudonym"),
                prev_hash=r["prev_hash"],
                entry_hash=r["entry_hash"],
            )
            for r in rows
        ]

    async def verify_chain(self, tenant_id: str) -> bool:
        """Prüft die Lückenlosigkeit (`seq_no` 1..N) und alle kryptografischen Verkettungen."""
        events = await self.list_events(tenant_id)
        expected_seq = 1
        expected_prev = "GENESIS"
        for ev in events:
            if ev.seq_no != expected_seq:
                raise AuditChainIntegrityError(
                    f"Sequenzlücke für '{tenant_id}': erwartet seq_no={expected_seq}, gefunden {ev.seq_no}"
                )
            if ev.prev_hash != expected_prev:
                raise AuditChainIntegrityError(
                    f"Kettenbruch bei seq_no={ev.seq_no}: prev_hash stimmt nicht mit Vorgänger überein."
                )
            recomputed = self.compute_entry_hash(
                tenant_id=ev.tenant_id,
                seq_no=ev.seq_no,
                run_id=ev.run_id,
                step_id=ev.step_id,
                stufe=ev.stufe,
                schutzklasse=ev.schutzklasse,
                path_taken=ev.path_taken,
                subject_pseudonym=ev.subject_pseudonym,
                prev_hash=ev.prev_hash,
            )
            if recomputed != ev.entry_hash:
                raise AuditChainIntegrityError(
                    f"Hash-Manipulation bei seq_no={ev.seq_no}: erwartet {recomputed}, gespeichert {ev.entry_hash}"
                )
            expected_seq += 1
            expected_prev = ev.entry_hash
        return True
