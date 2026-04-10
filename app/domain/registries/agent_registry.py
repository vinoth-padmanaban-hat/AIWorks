"""
Agent Registry — queries the agent_registry + agent_supported_skills tables
to find which agent service endpoint implements a given skill_id.

The Execution Engine uses this to route skill invocations at runtime.
No agent endpoints are hardcoded anywhere else.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class AgentManifest:
    agent_id: uuid.UUID
    display_name: str
    endpoint: str       # e.g. "http://localhost:8001"
    protocol: str       # "http_json"
    health_status: str
    version: str
    quality_score: float


class AgentRegistry:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_agents_for_skill(
        self,
        skill_id: str,
        tenant_id: uuid.UUID | None = None,
    ) -> list[AgentManifest]:
        """
        Return all active, non-offline agents that declare support for skill_id,
        ordered by quality_score DESC so the caller can pick the best one.
        """
        result = await self._db.execute(
            text(
                """
                SELECT
                    ar.agent_id,
                    ar.display_name,
                    ar.endpoint,
                    ar.protocol,
                    ar.health_status,
                    ar.version,
                    ass.quality_score
                FROM agent_registry ar
                JOIN agent_supported_skills ass
                    ON ar.agent_id = ass.agent_id
                WHERE ass.skill_id   = :skill_id
                  AND ar.active      = true
                  AND ar.health_status != 'OFFLINE'
                ORDER BY ass.quality_score DESC
                """
            ),
            {"skill_id": skill_id},
        )
        rows = result.fetchall()

        if not rows:
            logger.warning("No active agent found for skill_id=%s", skill_id)

        return [
            AgentManifest(
                agent_id=r.agent_id,
                display_name=r.display_name,
                endpoint=r.endpoint,
                protocol=r.protocol,
                health_status=r.health_status,
                version=r.version,
                quality_score=float(r.quality_score),
            )
            for r in rows
        ]

    async def get_agent(self, agent_id: uuid.UUID) -> AgentManifest | None:
        result = await self._db.execute(
            text(
                """
                SELECT ar.agent_id, ar.display_name, ar.endpoint,
                       ar.protocol, ar.health_status, ar.version,
                       1.0 AS quality_score
                FROM agent_registry ar
                WHERE ar.agent_id = :agent_id AND ar.active = true
                """
            ),
            {"agent_id": agent_id},
        )
        row = result.fetchone()
        if not row:
            return None
        return AgentManifest(
            agent_id=row.agent_id,
            display_name=row.display_name,
            endpoint=row.endpoint,
            protocol=row.protocol,
            health_status=row.health_status,
            version=row.version,
            quality_score=1.0,
        )
