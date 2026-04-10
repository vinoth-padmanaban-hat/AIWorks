"""Persona Store — load personas from the control plane DB."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.personas.models import PersonaRecord

_ROW_SQL = """
SELECT persona_id, tenant_id, display_name, COALESCE(slug, '') AS slug,
       COALESCE(role_description, '') AS role_description,
       COALESCE(tone_style, '') AS tone_style,
       COALESCE(goals, '[]'::jsonb) AS goals,
       COALESCE(constraints, '{}'::jsonb) AS constraints,
       COALESCE(default_skills, '{}') AS default_skills,
       COALESCE(guardrail_profile, '') AS guardrail_profile,
       active, COALESCE(is_default, FALSE) AS is_default
FROM personas
WHERE tenant_id = :tid AND active = TRUE
"""


def _row_to_record(row: Any) -> PersonaRecord:
    m = row._mapping
    raw_goals = m.get("goals")
    goals: list[Any] = raw_goals if isinstance(raw_goals, list) else []
    raw_constraints = m.get("constraints")
    constraints: dict[str, Any] = (
        dict(raw_constraints) if isinstance(raw_constraints, dict) else {}
    )
    raw_skills = m.get("default_skills")
    default_skills: list[str] = (
        [str(x) for x in raw_skills] if isinstance(raw_skills, (list, tuple)) else []
    )
    return PersonaRecord(
        persona_id=m["persona_id"],
        tenant_id=m["tenant_id"],
        display_name=m["display_name"],
        slug=(m.get("slug") or "") or "",
        role_description=m.get("role_description") or "",
        tone_style=m.get("tone_style") or "",
        goals=goals,
        constraints=constraints,
        default_skills=default_skills,
        guardrail_profile=m.get("guardrail_profile") or "",
        active=bool(m.get("active", True)),
        is_default=bool(m.get("is_default", False)),
    )


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(
        self, tenant_id: uuid.UUID, persona_id: uuid.UUID
    ) -> PersonaRecord | None:
        r = await self._db.execute(
            text(_ROW_SQL + " AND persona_id = :pid LIMIT 1"),
            {"tid": tenant_id, "pid": persona_id},
        )
        row = r.fetchone()
        return _row_to_record(row) if row else None

    async def resolve_for_execution(
        self,
        tenant_id: uuid.UUID,
        persona_id: uuid.UUID | None,
    ) -> PersonaRecord | None:
        """
        Resolve persona for an execution:
          - If persona_id is set: load that row (must belong to tenant and be active).
          - Else: prefer is_default=TRUE, else oldest active by created_at.
        """
        if persona_id is not None:
            return await self.get_by_id(tenant_id, persona_id)

        r = await self._db.execute(
            text(
                _ROW_SQL
                + """
  ORDER BY is_default DESC, created_at ASC
  LIMIT 1
"""
            ),
            {"tid": tenant_id},
        )
        row = r.fetchone()
        return _row_to_record(row) if row else None
