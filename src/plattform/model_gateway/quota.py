"""Zwei-Phasen-Token-Reservierung (`Pre-Authorize & Settle`, P5, LWP-04).
Verhindert Token-Budget-Überschreitungen bei parallelen Läufen eines Mandanten:
1. `pre_authorize(tenant_id, estimated_tokens)` reserviert das Kontingent vor dem LLM-Aufruf.
2. `settle(reservation_id, actual_tokens)` bucht den tatsächlichen Verbrauch ab und gibt die Differenz frei.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass


class QuotaExceededError(PermissionError):
    """Wird ausgelöst, wenn das Mandanten-Tokenbudget erschöpft ist."""


@dataclass
class TokenReservation:
    reservation_id: str
    tenant_id: str
    reserved_tokens: int
    settled: bool = False


class TwoPhaseTokenQuotaManager:
    """Thread- und Coroutine-sichere Zwei-Phasen-Token-Budgetverwaltung pro Mandant."""

    def __init__(self, default_daily_budget: int = 500_000) -> None:
        self.default_daily_budget = default_daily_budget
        self._budgets: dict[str, int] = {}
        self._used: dict[str, int] = {}
        self._reserved: dict[str, int] = {}
        self._reservations: dict[str, TokenReservation] = {}
        self._lock = asyncio.Lock()

    async def set_tenant_budget(self, tenant_id: str, daily_budget: int) -> None:
        async with self._lock:
            self._budgets[tenant_id] = daily_budget

    async def pre_authorize(self, tenant_id: str, estimated_tokens: int) -> TokenReservation:
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens muss >= 0 sein.")
        async with self._lock:
            limit = self._budgets.get(tenant_id, self.default_daily_budget)
            used = self._used.get(tenant_id, 0)
            reserved = self._reserved.get(tenant_id, 0)
            if used + reserved + estimated_tokens > limit:
                raise QuotaExceededError(
                    f"Token-Budget für Mandant '{tenant_id}' erschöpft: "
                    f"benötigt {estimated_tokens}, verfügbar {limit - used - reserved}."
                )
            self._reserved[tenant_id] = reserved + estimated_tokens
            res = TokenReservation(
                reservation_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                reserved_tokens=estimated_tokens,
            )
            self._reservations[res.reservation_id] = res
            return res

    async def settle(self, reservation_id: str, actual_tokens: int) -> int:
        if actual_tokens < 0:
            raise ValueError("actual_tokens muss >= 0 sein.")
        async with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None or res.settled:
                raise KeyError(f"Unbekannte oder bereits abgerechnete Reservierung: '{reservation_id}'")
            res.settled = True
            self._reserved[res.tenant_id] = max(
                0, self._reserved.get(res.tenant_id, 0) - res.reserved_tokens
            )
            self._used[res.tenant_id] = self._used.get(res.tenant_id, 0) + actual_tokens
            return self._used[res.tenant_id]

    async def get_usage(self, tenant_id: str) -> dict[str, int]:
        async with self._lock:
            limit = self._budgets.get(tenant_id, self.default_daily_budget)
            used = self._used.get(tenant_id, 0)
            reserved = self._reserved.get(tenant_id, 0)
            return {
                "budget": limit,
                "used": used,
                "reserved": reserved,
                "available": max(0, limit - used - reserved),
            }
