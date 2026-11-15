"""Abnahmetests für LWP-02: PostgreSQL-17-Schema, RLS-Pool & Audit-Hash-Kette mit Crypto-Shredding."""

import asyncio
from pathlib import Path

import pytest

from plattform.db import RLSDatabasePool, RLSViolationError
from plattform.evidence.audit import AuditChainIntegrityError, AuditWriter


def test_lwp_02_sql_migrations_contain_force_rls() -> None:
    sql_001 = Path("db/migrations/001_core_platform.sql").read_text(encoding="utf-8")
    sql_002 = Path("db/migrations/002_skills_and_lightweight.sql").read_text(encoding="utf-8")
    assert "FORCE ROW LEVEL SECURITY" in sql_001
    assert "FORCE ROW LEVEL SECURITY" in sql_002
    assert "skill_catalog" in sql_002
    assert "tenant_skill_bindings" in sql_002
    assert "skill_executions" in sql_002


async def test_lwp_02_rls_tenant_isolation() -> None:
    pool = RLSDatabasePool()
    await pool.register_tenant("stadt-a", "Stadt A")
    await pool.register_tenant("stadt-b", "Stadt B")

    async with pool.tenant_session("stadt-a") as s_a:
        assert "SET LOCAL app.current_tenant_id = 'stadt-a'" in s_a.executed_sql
        await s_a.insert("workflow_runs", {"workflow_id": "wohngeld", "status": "RUNNING"})

    async with pool.tenant_session("stadt-b") as s_b:
        rows_b = await s_b.select("workflow_runs")
        assert rows_b == []

        with pytest.raises(RLSViolationError):
            await s_b.insert(
                "workflow_runs",
                {"tenant_id": "stadt-a", "workflow_id": "cross_tenant_write", "status": "RUNNING"},
            )


async def test_lwp_02_100_parallel_audit_entries_and_crypto_shredding() -> None:
    pool = RLSDatabasePool()
    audit = AuditWriter(pool)

    async def write_one(idx: int) -> None:
        await audit.record_step(
            tenant_id="stadt-musterhausen",
            run_id=f"run-{idx}",
            step_id=f"step-{idx}",
            stufe=0,
            schutzklasse=2,
            path_taken="auto",
            subject_ref="buerger:max-mustermann-1980",
        )

    await asyncio.gather(*(write_one(i) for i in range(1, 101)))

    events = await audit.list_events("stadt-musterhausen")
    assert len(events) == 100
    assert [e.seq_no for e in events] == list(range(1, 101))
    assert await audit.verify_chain("stadt-musterhausen") is True

    # Art. 17 DSGVO Crypto-Shredding: Lösche das Subject-Salt
    old_pseudonym = events[0].subject_pseudonym
    deleted = await audit.crypto_shred_subject("stadt-musterhausen", "buerger:max-mustermann-1980")
    assert deleted == 1

    # Hash-Kette muss mathematisch zu 100 % valide bleiben
    assert await audit.verify_chain("stadt-musterhausen") is True

    # Ein neuer Eintrag für dieselbe Kennung erzeugt nun ein völlig anderes Pseudonym
    new_pseudonym = await audit.get_or_create_subject_pseudonym(
        "stadt-musterhausen", "buerger:max-mustermann-1980"
    )
    assert new_pseudonym != old_pseudonym


async def test_lwp_02_detects_tampering_in_audit_chain() -> None:
    pool = RLSDatabasePool()
    audit = AuditWriter(pool)
    await audit.record_step(
        tenant_id="stadt-x", run_id="r1", step_id="s1", stufe=0, schutzklasse=1
    )
    await audit.record_step(
        tenant_id="stadt-x", run_id="r1", step_id="s2", stufe=1, schutzklasse=1
    )

    # Manipuliere einen Eintrag direkt im Speicher
    pool._tables["audit_events"][0]["path_taken"] = "human"
    with pytest.raises(AuditChainIntegrityError):
        await audit.verify_chain("stadt-x")
