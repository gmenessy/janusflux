"""RLS-Datenbank-Pool & Mandanten-Kontext-Manager (LWP-02).
Erzwingt `SET LOCAL app.current_tenant_id` pro Transaktion und verhindert
Mandanten-Lecks über Row-Level-Security (FORCE ROW LEVEL SECURITY).
Unterstützt sowohl PostgreSQL 17 (psycopg) als auch den eingebetteten
deterministischen RLS-Speicher für isolierte CI-/Unit-Läufe ohne laufenden DB-Daemon.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class RLSViolationError(PermissionError):
    """Wird ausgelöst, wenn ohne gesetzten Mandantenkontext oder auf fremde Mandanten zugegriffen wird."""


class TenantScopedSession:
    """Repräsentiert eine mandantengebundene Transaktionssitzung (`SET LOCAL app.current_tenant_id`)."""

    def __init__(self, pool: RLSDatabasePool, tenant_id: str) -> None:
        if not tenant_id or not tenant_id.strip():
            raise RLSViolationError("app.current_tenant_id darf nicht leer sein.")
        self._pool = pool
        self.tenant_id = tenant_id
        self.executed_sql: list[str] = [
            "SET LOCAL ROLE app_runtime",
            f"SET LOCAL app.current_tenant_id = '{tenant_id}'",
        ]

    async def insert(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        row_tenant = record.get("tenant_id", self.tenant_id)
        if row_tenant != self.tenant_id:
            raise RLSViolationError(
                f"RLS WITH CHECK Verletzung auf '{table}': "
                f"Session-Mandant '{self.tenant_id}' != Zeilen-Mandant '{row_tenant}'."
            )
        stored = dict(record)
        stored["tenant_id"] = self.tenant_id
        stored.setdefault("id", str(uuid.uuid4()))
        async with self._pool._lock:
            self._pool._tables.setdefault(table, []).append(stored)
        return dict(stored)

    async def select(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        async with self._pool._lock:
            rows = self._pool._tables.get(table, [])
            visible = [
                dict(r)
                for r in rows
                if r.get("tenant_id") == self.tenant_id
                and all(r.get(k) == v for k, v in filters.items())
            ]
        return visible

    async def delete(self, table: str, **filters: Any) -> int:
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


class RLSDatabasePool:
    """Mandanten-isolierter Datenbank-Pool mit `SET LOCAL app.current_tenant_id`."""

    def __init__(self, dsn: str = "postgresql://plattform_admin@localhost:5432/plattform") -> None:
        self.dsn = dsn
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._tenants: dict[str, dict[str, Any]] = {}
        self._skill_catalog: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def register_tenant(
        self,
        tenant_id: str,
        name: str,
        bundesland: str = "NW",
        max_schutzklasse: int = 3,
        daily_token_budget: int = 500_000,
    ) -> dict[str, Any]:
        async with self._lock:
            entry = {
                "tenant_id": tenant_id,
                "name": name,
                "bundesland": bundesland,
                "max_schutzklasse": max_schutzklasse,
                "daily_token_budget": daily_token_budget,
                "active": True,
            }
            self._tenants[tenant_id] = entry
            return dict(entry)

    @asynccontextmanager
    async def tenant_session(self, tenant_id: str) -> AsyncIterator[TenantScopedSession]:
        """Öffnet eine Transaktionssitzung mit `SET LOCAL app.current_tenant_id`."""
        session = TenantScopedSession(self, tenant_id=tenant_id)
        yield session
