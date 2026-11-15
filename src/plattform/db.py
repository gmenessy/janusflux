"""RLS-Datenbank-Pool & Mandanten-Kontext-Manager (LWP-02 / LWP-15).
Erzwingt `SET LOCAL app.current_tenant_id` und `SET LOCAL ROLE app_runtime` pro Transaktion
und verhindert Mandanten-Lecks über Row-Level-Security (`FORCE ROW LEVEL SECURITY`).
Unterstützt:
1. Live-PostgreSQL-Datenbank über `psycopg` (`use_postgres=True`), inkl. automatischer
   Ausführung von `db/migrations/001_core_platform.sql` und `002_skills_and_lightweight.sql`.
2. Eingebetteten deterministischen RLS-Speicher (`use_postgres=False`, Standard für schnelle
   Unit-Tests ohne laufenden DB-Daemon).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pg_sql
from psycopg.rows import dict_row


class RLSViolationError(PermissionError):
    """Wird ausgelöst, wenn ohne gesetzten Mandantenkontext oder auf fremde Mandanten zugegriffen wird."""


ALLOWED_TABLES: set[str] = {
    "tenants",
    "ai_act_register",
    "workflow_runs",
    "workflow_steps",
    "approvals",
    "audit_subject_salts",
    "audit_sequence_heads",
    "audit_events",
    "skill_catalog",
    "tenant_skill_bindings",
    "skill_executions",
}


class TenantScopedSession:
    """Repräsentiert eine mandantengebundene Transaktionssitzung (`SET LOCAL app.current_tenant_id`)."""

    def __init__(
        self,
        pool: RLSDatabasePool,
        tenant_id: str,
        pg_conn: psycopg.AsyncConnection[dict[str, Any]] | None = None,
    ) -> None:
        if not tenant_id or not tenant_id.strip():
            raise RLSViolationError("app.current_tenant_id darf nicht leer sein.")
        self._pool = pool
        self.tenant_id = tenant_id
        self._pg_conn = pg_conn
        self.executed_sql: list[str] = [
            "SET LOCAL ROLE app_runtime",
            f"SET LOCAL app.current_tenant_id = '{tenant_id}'",
        ]

    async def insert(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unbekannte Tabelle: '{table}'")
        row_tenant = record.get("tenant_id", self.tenant_id)
        if row_tenant != self.tenant_id:
            raise RLSViolationError(
                f"RLS WITH CHECK Verletzung auf '{table}': "
                f"Session-Mandant '{self.tenant_id}' != Zeilen-Mandant '{row_tenant}'."
            )

        if self._pg_conn is not None:
            return await self._pg_insert(table, record)

        stored = dict(record)
        stored["tenant_id"] = self.tenant_id
        stored.setdefault("id", str(uuid.uuid4()))
        async with self._pool._lock:
            self._pool._tables.setdefault(table, []).append(stored)
        return dict(stored)

    async def _pg_insert(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        assert self._pg_conn is not None
        payload = dict(record)
        payload["tenant_id"] = self.tenant_id

        # Konvertiere salt_hex -> salt_bytes für PostgreSQL BYTEA in audit_subject_salts
        if table == "audit_subject_salts" and "salt_hex" in payload and "salt_bytes" not in payload:
            payload["salt_bytes"] = bytes.fromhex(str(payload.pop("salt_hex")))

        cols = list(payload.keys())
        vals = [
            json.dumps(v) if isinstance(v, dict) else v
            for v in (payload[c] for c in cols)
        ]
        query = pg_sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            pg_sql.Identifier(table),
            pg_sql.SQL(", ").join(pg_sql.Identifier(c) for c in cols),
            pg_sql.SQL(", ").join(pg_sql.Placeholder() for _ in cols),
        )
        try:
            async with self._pg_conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, vals)
                row = await cur.fetchone()
                out = dict(row) if row else dict(payload)
                if table == "audit_subject_salts" and "salt_bytes" in out:
                    raw_b = out["salt_bytes"]
                    out["salt_hex"] = bytes(raw_b).hex()
                return out
        except psycopg.errors.InsufficientPrivilege as exc:
            raise RLSViolationError(f"PostgreSQL RLS Policy Violation: {exc}") from exc

    async def select(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unbekannte Tabelle: '{table}'")
        if self._pg_conn is not None:
            return await self._pg_select(table, **filters)

        async with self._pool._lock:
            rows = self._pool._tables.get(table, [])
            visible = [
                dict(r)
                for r in rows
                if r.get("tenant_id") == self.tenant_id
                and all(r.get(k) == v for k, v in filters.items())
            ]
        return visible

    async def _pg_select(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        assert self._pg_conn is not None
        where_parts: list[pg_sql.Composable] = []
        vals: list[Any] = []
        for k, v in filters.items():
            where_parts.append(
                pg_sql.SQL("{} = {}").format(pg_sql.Identifier(k), pg_sql.Placeholder())
            )
            vals.append(v)

        if where_parts:
            query = pg_sql.SQL("SELECT * FROM {} WHERE {}").format(
                pg_sql.Identifier(table),
                pg_sql.SQL(" AND ").join(where_parts),
            )
        else:
            query = pg_sql.SQL("SELECT * FROM {}").format(pg_sql.Identifier(table))

        async with self._pg_conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, vals)
            rows = await cur.fetchall()
            result: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                if table == "audit_subject_salts" and "salt_bytes" in d:
                    d["salt_hex"] = bytes(d["salt_bytes"]).hex()
                result.append(d)
            return result

    async def delete(self, table: str, **filters: Any) -> int:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unbekannte Tabelle: '{table}'")
        if self._pg_conn is not None:
            return await self._pg_delete(table, **filters)

        async with self._pool._lock:
            rows = self._pool._tables.get(table, [])
            kept: list[dict[str, Any]] = []
            deleted = 0
            for r in rows:
                if r.get("tenant_id") == self.tenant_id and all(
                    r.get(k) == v for k, v in filters.items()
                ):
                    deleted += 1
                else:
                    kept.append(r)
            self._pool._tables[table] = kept
        return deleted

    async def _pg_delete(self, table: str, **filters: Any) -> int:
        assert self._pg_conn is not None
        where_parts: list[pg_sql.Composable] = []
        vals: list[Any] = []
        for k, v in filters.items():
            where_parts.append(
                pg_sql.SQL("{} = {}").format(pg_sql.Identifier(k), pg_sql.Placeholder())
            )
            vals.append(v)

        if where_parts:
            query = pg_sql.SQL("DELETE FROM {} WHERE {}").format(
                pg_sql.Identifier(table),
                pg_sql.SQL(" AND ").join(where_parts),
            )
        else:
            query = pg_sql.SQL("DELETE FROM {}").format(pg_sql.Identifier(table))

        async with self._pg_conn.cursor() as cur:
            await cur.execute(query, vals)
            return cur.rowcount or 0


class RLSDatabasePool:
    """Mandanten-isolierter Datenbank-Pool mit `SET LOCAL app.current_tenant_id`."""

    def __init__(
        self,
        dsn: str = "postgresql://plattform_admin@localhost:5432/plattform",
        use_postgres: bool = False,
    ) -> None:
        self.dsn = dsn
        self.use_postgres = use_postgres
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._tenants: dict[str, dict[str, Any]] = {}
        self._skill_catalog: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def apply_migrations(self, migrations_dir: Path = Path("db/migrations")) -> None:
        """Wendet die SQL-Migrationen `001` und `002` auf eine Live-PostgreSQL-Instanz an."""
        if not self.use_postgres:
            return
        conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
        try:
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                sql_text = sql_file.read_text(encoding="utf-8")
                await conn.execute(sql_text)
        finally:
            await conn.close()

    async def register_skill_in_catalog(
        self,
        *,
        skill_id: str,
        version: str,
        name: str,
        art: str,
        wirkung: str,
        stufen: list[int],
        max_schutzklasse: int,
        manifest_json: dict[str, Any],
        manifest_sha256: str,
    ) -> None:
        if self.use_postgres:
            conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
            try:
                await conn.execute(
                    """
                    INSERT INTO skill_catalog (
                        skill_id, version, name, art, wirkung, stufen,
                        max_schutzklasse, manifest_json, manifest_sha256
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (skill_id, version) DO NOTHING
                    """,
                    (
                        skill_id,
                        version,
                        name,
                        art,
                        wirkung,
                        stufen,
                        max_schutzklasse,
                        json.dumps(manifest_json),
                        manifest_sha256,
                    ),
                )
            finally:
                await conn.close()
        else:
            async with self._lock:
                self._skill_catalog[(skill_id, version)] = {
                    "skill_id": skill_id,
                    "version": version,
                    "name": name,
                }

    async def register_tenant(
        self,
        tenant_id: str,
        name: str,
        bundesland: str = "NW",
        max_schutzklasse: int = 3,
        daily_token_budget: int = 500_000,
    ) -> dict[str, Any]:
        entry = {
            "tenant_id": tenant_id,
            "name": name,
            "bundesland": bundesland,
            "max_schutzklasse": max_schutzklasse,
            "daily_token_budget": daily_token_budget,
            "active": True,
        }
        if self.use_postgres:
            conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
            try:
                await conn.execute(
                    """
                    INSERT INTO tenants (tenant_id, name, bundesland, max_schutzklasse, daily_token_budget, active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (tenant_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        bundesland = EXCLUDED.bundesland,
                        max_schutzklasse = EXCLUDED.max_schutzklasse,
                        daily_token_budget = EXCLUDED.daily_token_budget
                    """,
                    (tenant_id, name, bundesland, max_schutzklasse, daily_token_budget),
                )
            finally:
                await conn.close()

        async with self._lock:
            self._tenants[tenant_id] = entry
            return dict(entry)

    @asynccontextmanager
    async def tenant_session(self, tenant_id: str) -> AsyncIterator[TenantScopedSession]:
        """Öffnet eine Transaktionssitzung mit `SET LOCAL ROLE app_runtime` und `SET LOCAL app.current_tenant_id`."""
        if not self.use_postgres:
            session = TenantScopedSession(self, tenant_id=tenant_id)
            yield session
            return

        conn: psycopg.AsyncConnection[dict[str, Any]] = await psycopg.AsyncConnection.connect(
            self.dsn, autocommit=False, row_factory=dict_row
        )
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE app_runtime")
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', %s, true)",
                    (tenant_id,),
                )
                session = TenantScopedSession(self, tenant_id=tenant_id, pg_conn=conn)
                yield session
        finally:
            await conn.close()
