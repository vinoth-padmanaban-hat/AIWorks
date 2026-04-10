import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.orchestrator import Orchestrator
from app.domain.models.invocation import AgentInvocationResult

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post(
    "/run/{tenant_id}",
    response_model=AgentInvocationResult,
    summary="Trigger content ingestion for a tenant",
    description=(
        "Starts one ingestion execution: load Persona → Orchestrator → Execution Engine → "
        "Agent Registry lookup → HTTP POST to content_ingestion agent. "
        "Optional `persona_id` selects a persona; omit to use the tenant default."
    ),
)
async def trigger_ingestion(
    tenant_id: uuid.UUID,
    persona_id: uuid.UUID | None = Query(
        default=None,
        description="Persona Store id; defaults to is_default persona for the tenant.",
    ),
    db: AsyncSession = Depends(get_db),
) -> AgentInvocationResult:
    orchestrator = Orchestrator(db)
    return await orchestrator.run_content_ingestion(tenant_id=tenant_id, persona_id=persona_id)
