"""
Content Ingestion Agent Service — independent FastAPI app (port 8001).

The Execution Engine (control plane) calls:
  POST /invoke  with AgentInvocationContext

This service:
  1. Validates the AgentInvocationContext.
  2. Resolves the tenant's DB via TenantDBResolver (reads control plane tenant_db_connections).
  3. Creates the ingestion_executions row in the TENANT DB.
  4. Runs the LangGraph ingestion graph (which uses the tenant DB for all domain data).
  5. Returns AgentInvocationResult.

DB split:
  - Control plane DB : AgentInvocationContext policy/registry data arrives pre-resolved.
  - Tenant DB        : ingestion_executions, articles, tags, logs — no tenant_id columns.

To run:
  uv run uvicorn agents.content_ingestion.main:app --port 8001 --reload
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
from agents.content_ingestion.graph import run_ingestion_graph

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

SUPPORTED_SKILLS = {"content_ingestion"}

_SEP = "=" * 72

app = FastAPI(
    title="Content Ingestion Agent",
    version="0.2.0",
    description=(
        "LangGraph agent: scrape (BFS nested) → normalize → tag → format articles. "
        "Uses per-tenant Postgres DB — no tenant_id columns in domain tables."
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
    summary="Invoke a skill on this agent",
)
async def invoke(ctx: AgentInvocationContext) -> AgentInvocationResult:
    logger.info(_SEP)
    logger.info(
        "INVOKE  skill=%-30s  execution_id=%s  tenant=%s",
        ctx.skill_id, ctx.execution_id, ctx.tenant_id,
    )

    # ── Skill validation ──────────────────────────────────────────────────────
    if ctx.skill_id not in SUPPORTED_SKILLS:
        logger.warning("Unsupported skill requested: %s", ctx.skill_id)
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error=(
                f"Skill '{ctx.skill_id}' is not supported by this agent. "
                f"Supported: {sorted(SUPPORTED_SKILLS)}"
            ),
        )

    tenant_id_str    = ctx.skill_input.get("tenant_id")
    execution_id_str = ctx.skill_input.get("execution_id")

    if not tenant_id_str:
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error="skill_input must include 'tenant_id'",
        )

    tenant_id    = uuid.UUID(tenant_id_str)
    execution_id = uuid.UUID(execution_id_str) if execution_id_str else ctx.execution_id

    logger.info(
        "  tenant_id=%s  execution_id=%s", tenant_id, execution_id
    )

    # ── Create ingestion_executions row in the TENANT DB ─────────────────────
    # Note: no tenant_id column — the DB itself is the tenant boundary.
    try:
        async with get_tenant_db_session(tenant_id) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO ingestion_executions (execution_id, started_at, status, persona_id)
                    VALUES (:eid, now(), 'RUNNING', :pid)
                    ON CONFLICT (execution_id) DO NOTHING
                    """
                ),
                {"eid": execution_id, "pid": ctx.persona_id},
            )
            await db.commit()
        logger.info(
            "  ingestion_executions row created  persona_id=%s",
            ctx.persona_id,
        )
    except Exception as exc:
        logger.exception(
            "  Failed to connect to tenant DB for tenant=%s: %s", tenant_id, exc
        )
        return AgentInvocationResult(
            execution_id=ctx.execution_id,
            step_id=ctx.step_id,
            skill_id=ctx.skill_id,
            status="ERROR",
            error=f"Could not connect to tenant DB: {exc}",
        )

    # ── Run the ingestion graph ───────────────────────────────────────────────
    try:
        persona_payload: dict | None = None
        if ctx.persona is not None:
            persona_payload = ctx.persona.model_dump(mode="json")

        summary = await run_ingestion_graph(
            tenant_id=tenant_id,
            execution_id=execution_id,
            effective_policy=ctx.effective_policy,
            persona_id=ctx.persona_id,
            persona=persona_payload,
            persona_summary=ctx.persona_summary,
        )
        logger.info(
            "  DONE  new_articles=%d  cost=$%.6f  status=SUCCESS",
            summary.get("new_articles", 0),
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
        logger.exception(
            "  FAILED  execution_id=%s  tenant=%s  error=%s",
            execution_id, tenant_id, exc,
        )
        # Mark execution as ERROR in tenant DB
        try:
            async with get_tenant_db_session(tenant_id) as db:
                await db.execute(
                    text(
                        "UPDATE ingestion_executions "
                        "SET status='ERROR', finished_at=now() "
                        "WHERE execution_id = :eid"
                    ),
                    {"eid": execution_id},
                )
                await db.commit()
        except Exception:
            logger.warning("  Could not update execution status to ERROR")

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
        "status":           "ok",
        "agent":            "content_ingestion",
        "version":          "0.2.0",
        "supported_skills": list(SUPPORTED_SKILLS),
        "db_mode":          "per-tenant-db",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agents.content_ingestion.main:app",
        host="0.0.0.0",
        port=settings.content_ingestion_agent_port,
        reload=True,
    )
