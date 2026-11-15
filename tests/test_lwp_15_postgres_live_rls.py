"""Abnahmetests für LWP-15: Live-PostgreSQL-Integration mit realer `FORCE ROW LEVEL SECURITY`,
`SET LOCAL ROLE app_runtime`, `app.current_tenant_id`, `AuditWriter` und `onboard_all_tenants`.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from plattform.db import RLSDatabasePool, RLSViolationError
from plattform.evidence.audit import AuditWriter
from scripts.onboard_all_tenants import onboard_all_tenants


def _find_pg_bindir() -> Path | None:
    pg_root = Path("/usr/lib/postgresql")
    if not pg_root.exists():
        return None
    for child in sorted(pg_root.iterdir(), reverse=True):
        bindir = child / "bin"
        if (bindir / "initdb").exists() and (bindir / "pg_ctl").exists():
            return bindir
    return None


def _start_temp_pg(pg_bin: Path, pgdata: Path, sock_dir: Path, log_file: Path, port: int) -> None:
    subprocess.run(
        [
            str(pg_bin / "initdb"),
            "-D",
            str(pgdata),
            "-U",
            "plattform_admin",
            "--auth=trust",
            "--no-instructions",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(pg_bin / "pg_ctl"),
            "-D",
            str(pgdata),
            "-l",
            str(log_file),
            "-o",
            f"-k {sock_dir} -p {port} -h 127.0.0.1",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(pg_bin / "createdb"),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "plattform_admin",
            "plattform",
        ],
        check=True,
        capture_output=True,
    )


def _stop_temp_pg(pg_bin: Path, pgdata: Path) -> None:
    subprocess.run(
        [str(pg_bin / "pg_ctl"), "-D", str(pgdata), "-m", "immediate", "stop"],
        check=False,
        capture_output=True,
    )
    shutil.rmtree(pgdata, ignore_errors=True)


async def test_lwp_15_live_postgresql_rls_and_audit_chain(tmp_path: Path) -> None:
    pg_bin = _find_pg_bindir()
    if pg_bin is None:
        pytest.skip("Lokale PostgreSQL-Server-Binaries nicht installiert.")

    pgdata = tmp_path / "pgdata"
    sock_dir = tmp_path / "pgsock"
    sock_dir.mkdir(parents=True, exist_ok=True)
    port = 55439

    await asyncio.to_thread(_start_temp_pg, pg_bin, pgdata, sock_dir, tmp_path / "pg.log", port)

    try:
        dsn = f"postgresql://plattform_admin@127.0.0.1:{port}/plattform"
        pool = RLSDatabasePool(dsn=dsn, use_postgres=True)
        await pool.apply_migrations(Path("db/migrations"))

        await pool.register_tenant("stadt-a", "Stadt A", bundesland="NW")
        await pool.register_tenant("stadt-b", "Stadt B", bundesland="BY")

        # 1. Mandant A legt einen Lauf in PostgreSQL an
        async with pool.tenant_session("stadt-a") as s_a:
            inserted = await s_a.insert(
                "workflow_runs",
                {
                    "workflow_id": "wohngeld_vorpruefung",
                    "schutzklasse": 2,
                    "status": "RUNNING",
                },
            )
            assert inserted["tenant_id"] == "stadt-a"

        # 2. Mandant B sieht dank PostgreSQL FORCE ROW LEVEL SECURITY 0 Zeilen von Mandant A
        async with pool.tenant_session("stadt-b") as s_b:
            rows_b = await s_b.select("workflow_runs")
            assert rows_b == []

            with pytest.raises(RLSViolationError):
                await s_b.insert(
                    "workflow_runs",
                    {
                        "tenant_id": "stadt-a",
                        "workflow_id": "unerlaubt",
                        "schutzklasse": 1,
                        "status": "RUNNING",
                    },
                )

        # 3. Vollständige Audit-Hash-Kette + Art. 17 DSGVO Crypto-Shredding direkt in PostgreSQL
        audit = AuditWriter(pool)
        for i in range(1, 11):
            await audit.record_step(
                tenant_id="stadt-a",
                run_id=f"run-{i}",
                step_id=f"step-{i}",
                stufe=0,
                schutzklasse=2,
                path_taken="auto",
                subject_ref="buerger:echt-db-test",
            )
        assert await audit.verify_chain("stadt-a") is True
        shredded = await audit.crypto_shred_subject("stadt-a", "buerger:echt-db-test")
        assert shredded == 1
        assert await audit.verify_chain("stadt-a") is True

        # 4. Mandanten-Onboarding gegen echte PostgreSQL-Tabellen
        onboarded = await onboard_all_tenants(
            tenants_dir=Path("tenants"),
            skills_dir=Path("skills"),
            db_pool=pool,
        )
        assert "stadt-musterhausen" in onboarded
        async with pool.tenant_session("stadt-musterhausen") as s_m:
            bindings = await s_m.select("tenant_skill_bindings")
            assert len(bindings) == 5
    finally:
        await asyncio.to_thread(_stop_temp_pg, pg_bin, pgdata)
