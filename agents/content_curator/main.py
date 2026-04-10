"""
Content Curator Agent Service — independent FastAPI app (port 8003).

Implements the `content_curation` skill:
  scrape tenant sources → extract articles → match products → generate newsletter

The Execution Engine (control plane) calls POST /invoke with AgentInvocationContext.

To run:
  uv run uvicorn agents.content_curator.main:app --port 8003 --reload
"""

import logging
import uuid

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import settings
from app.core.langfuse_setup import flush_langfuse, init_langfuse
from app.core.tenant_db import get_tenant_db_session
from app.domain.models.invocation import (
    AgentInvocationContext,
    AgentInvocationResult,
    CostMetrics,
)
from agents.content_curator.graph import run_curation_graph

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

SUPPORTED_SKILLS = {"content_curation"}

app = FastAPI(
    title="Content Curator Agent",
    version="1.0.0",
    description=(
        "LangGraph agent: scrape → extract → match products → generate newsletter articles. "
        "Uses per-tenant Postgres DB."
    ),
)


@app.on_event("startup")
async def _langfuse_startup() -> None:
    init_langfuse()


@app.on_event("shutdown")
async def _langfuse_shutdown() -> None:
    flush_langfuse()


@app.post(
    "/invoke",
    response_model=AgentInvocationResult,
    summary="Invoke content curation skill",
)
async def invoke(ctx: AgentInvocationContext) -> AgentInvocationResult:
    logger.info(
        "INVOKE  skill=%-30s  execution=%s  tenant=%s",
        ctx.skill_id, ctx.execution_id, ctx.tenant_id,
    )

    if ctx.skill_id not in SUPPORTED_SKILLS:
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error=(
                f"Skill '{ctx.skill_id}' not supported. "
                f"Supported: {sorted(SUPPORTED_SKILLS)}"
            ),
        )

    tenant_id_str = ctx.skill_input.get("tenant_id")
    execution_id_str = ctx.skill_input.get("execution_id")

    if not tenant_id_str:
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error="skill_input must include 'tenant_id'",
        )

    tenant_id = uuid.UUID(tenant_id_str)
    execution_id = uuid.UUID(execution_id_str) if execution_id_str else ctx.execution_id

    try:
        persona_payload: dict | None = None
        if ctx.persona is not None:
            persona_payload = ctx.persona.model_dump(mode="json")

        summary = await run_curation_graph(
            tenant_id=tenant_id,
            execution_id=execution_id,
            goal=ctx.goal,
            effective_policy=ctx.effective_policy,
            persona_id=ctx.persona_id,
            persona=persona_payload,
            persona_summary=ctx.persona_summary,
        )

        logger.info(
            "  DONE  newsletters=%d  cost=$%.6f",
            summary.get("newsletter_articles_created", 0),
            summary.get("estimated_cost_usd", 0.0),
        )
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="SUCCESS",
            output=summary,
            cost_metrics=CostMetrics(
                tokens_in=summary.get("total_tokens_in", 0),
                tokens_out=summary.get("total_tokens_out", 0),
                cost_usd=summary.get("estimated_cost_usd", 0.0),
            ),
        )

    except Exception as exc:
        logger.exception("  FAILED  execution=%s  error=%s", execution_id, exc)
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error=str(exc),
        )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "agent": "content_curator",
        "version": "1.0.0",
        "supported_skills": list(SUPPORTED_SKILLS),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agents.content_curator.main:app",
        host="0.0.0.0",
        port=settings.content_curator_agent_port,
        reload=True,
    )
