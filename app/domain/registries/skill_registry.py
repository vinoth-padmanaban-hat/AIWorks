"""
Skill Registry — queries the skill_registry table for available skills.

Used by:
  - Planner: to know which skills exist and what they do.
  - Orchestrator: to filter skills by policy before planning.
  - Admin API: to list registered skills.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy.models import EffectivePolicy

logger = logging.getLogger(__name__)


@dataclass
class SkillManifest:
    skill_id: str
    name: str
    description: str
    domain: str
    tags: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    active: bool = True


class SkillRegistry:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_skills(
        self,
        *,
        domain: str | None = None,
        tags: list[str] | None = None,
        active_only: bool = True,
    ) -> list[SkillManifest]:
        """
        Return all skills matching the optional filters.
        Used by the planner to build its action vocabulary.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if active_only:
            clauses.append("active = true")

        if domain:
            clauses.append("domain = :domain")
            params["domain"] = domain

        if tags:
            clauses.append("tags && :tags")
            params["tags"] = tags

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        result = await self._db.execute(
            text(
                f"SELECT skill_id, name, description, domain, tags, "
                f"       input_schema, output_schema, active "
                f"FROM skill_registry {where} "
                f"ORDER BY skill_id"
            ),
            params,
        )
        rows = result.fetchall()
        return [
            SkillManifest(
                skill_id=r.skill_id,
                name=r.name,
                description=r.description,
                domain=r.domain,
                tags=list(r.tags) if r.tags else [],
                input_schema=dict(r.input_schema) if r.input_schema else {},
                output_schema=dict(r.output_schema) if r.output_schema else {},
                active=r.active,
            )
            for r in rows
        ]

    async def get_skill(self, skill_id: str) -> SkillManifest | None:
        result = await self._db.execute(
            text(
                "SELECT skill_id, name, description, domain, tags, "
                "       input_schema, output_schema, active "
                "FROM skill_registry WHERE skill_id = :sid"
            ),
            {"sid": skill_id},
        )
        r = result.fetchone()
        if not r:
            return None
        return SkillManifest(
            skill_id=r.skill_id,
            name=r.name,
            description=r.description,
            domain=r.domain,
            tags=list(r.tags) if r.tags else [],
            input_schema=dict(r.input_schema) if r.input_schema else {},
            output_schema=dict(r.output_schema) if r.output_schema else {},
            active=r.active,
        )

    async def filter_by_policy(
        self,
        skills: list[SkillManifest],
        effective_policy: EffectivePolicy | dict[str, Any],
    ) -> list[SkillManifest]:
        """
        Filter a list of skills based on the tenant's effective policy.
        Removes blocked skills, and when defaultAllow=False, only keeps
        explicitly allowed ones.
        """
        # Accept both EffectivePolicy (new) and plain dict (legacy callers)
        if isinstance(effective_policy, EffectivePolicy):
            blocked: list[str] = effective_policy.blocked_skills
            allowed: list[str] = effective_policy.allowed_skills
            default_allow: bool = effective_policy.default_allow
        else:
            caps = effective_policy.get("capabilities", {})
            blocked = caps.get("blocked", [])
            allowed = caps.get("allowed", [])
            default_allow = caps.get("defaultAllow", True)

        result: list[SkillManifest] = []
        for skill in skills:
            if skill.skill_id in blocked:
                logger.debug("Skill %s blocked by policy", skill.skill_id)
                continue
            if not default_allow and skill.skill_id not in allowed:
                logger.debug(
                    "Skill %s not in allowed list (defaultAllow=false)", skill.skill_id
                )
                continue
            result.append(skill)

        return result
