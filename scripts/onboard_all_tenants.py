"""Mandanten-Onboarding-Skript (LWP-12).
Lädt alle `tenants/<tenant_id>/tenant.yaml`, prüft die freigeschalteten Skills gegen die
`SkillRegistry`, registriert den Mandanten im `RLSDatabasePool` und legt die
`tenant_skill_bindings` sowie `ai_act_register`-Einträge mandantenisoliert an.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from plattform.db import RLSDatabasePool
from plattform.skills.registry import SkillRegistry


async def onboard_all_tenants(
    tenants_dir: Path = Path("tenants"),
    skills_dir: Path = Path("skills"),
    db_pool: RLSDatabasePool | None = None,
) -> dict[str, dict[str, Any]]:
    pool = db_pool or RLSDatabasePool()
    registry = SkillRegistry(skills_dir)
    registry.load_all()

    onboarded: dict[str, dict[str, Any]] = {}
    if not tenants_dir.exists():
        return onboarded

    for manifest_path in sorted(tenants_dir.rglob("tenant.yaml")):
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        tenant_id = str(raw["tenant_id"])
        await pool.register_tenant(
            tenant_id=tenant_id,
            name=str(raw["name"]),
            bundesland=str(raw.get("bundesland", "NW")),
            max_schutzklasse=int(raw.get("max_schutzklasse", 3)),
            daily_token_budget=int(raw.get("daily_token_budget", 500_000)),
        )

        enabled_skills: list[str] = []
        async with pool.tenant_session(tenant_id) as session:
            for item in raw.get("enabled_skills", []):
                sid = str(item["skill_id"])
                ver = str(item["version"])
                manifest = registry.get(sid)
                if manifest.version != ver:
                    raise ValueError(
                        f"Versionskonflikt für Skill '{sid}': erwartet {ver}, gefunden {manifest.version}"
                    )
                await session.insert(
                    "tenant_skill_bindings",
                    {
                        "tenant_id": tenant_id,
                        "skill_id": sid,
                        "skill_version": ver,
                        "enabled": True,
                        "approved_by_l3": str(raw.get("approved_by_l3", "system")),
                    },
                )
                enabled_skills.append(sid)

            for sys_entry in raw.get("ai_act_systems", []):
                await session.insert(
                    "ai_act_register",
                    {
                        "tenant_id": tenant_id,
                        "system_id": str(sys_entry["system_id"]),
                        "risikoklasse": str(sys_entry["risikoklasse"]),
                        "approved_by_l3": str(raw.get("approved_by_l3", "system")),
                    },
                )

        onboarded[tenant_id] = {
            "tenant_id": tenant_id,
            "enabled_skills": enabled_skills,
            "max_schutzklasse": int(raw.get("max_schutzklasse", 3)),
        }

    return onboarded


def main() -> int:
    res = asyncio.run(onboard_all_tenants())
    print(f"[ONBOARD-OK] {len(res)} Mandant(en) erfolgreich eingerichtet: {list(res.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
