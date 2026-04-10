"""
Generic execution endpoint — the primary API surface for all tenant work.

POST /execute
  Body: ExecuteRequest (tenant_id, goal, optional persona_id / skill_ids)
  Response: ExecuteResponse (execution_id, plan, step results, cost)

This replaces domain-specific routes like /ingestion/run/{tenant_id}.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.orchestrator import Orchestrator
from app.domain.models.invocation import ExecuteRequest, ExecuteResponse

router = APIRouter(tags=["execute"])


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    summary="Execute a goal for a tenant",
    description=(
        "Generic entry point: resolve persona → load policy → plan skills → "
        "dispatch to agents → return results. Works for any domain / use case."
    ),
)
async def execute(
    request: ExecuteRequest,
    db: AsyncSession = Depends(get_db),
) -> ExecuteResponse:
    orchestrator = Orchestrator(db)
    return await orchestrator.execute(request)
