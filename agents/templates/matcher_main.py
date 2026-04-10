"""
Generic Content Matcher Agent — FastAPI service (port 8005).

Wraps matcher_graph.py as an HTTP service so the Execution Engine can
dispatch to it via the Agent Registry.

To run:
  uv run uvicorn agents.templates.matcher_main:app --port 8005 --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app.core.langfuse_setup import flush_langfuse, init_langfuse
from app.core.logging import get_logger
from agents.templates.matcher_graph import run_matcher_agent
from agents.templates.matcher_models import MatcherAgentInput, MatcherAgentOutput

logger = get_logger("generic_content_matcher_agent")

app = FastAPI(
    title="Generic Content Matcher Agent",
    version="1.0.0",
    description=(
        "Reusable content-to-entity matching agent. "
        "Combines vector search + DB lookup + LLM re-ranking. "
        "Works for products, KB articles, legal cases, HR policies — any entity type."
    ),
)


@app.on_event("startup")
async def _langfuse_startup() -> None:
    init_langfuse()


@app.on_event("shutdown")
async def _langfuse_shutdown() -> None:
    flush_langfuse()


class RunRequest(BaseModel):
    input: MatcherAgentInput


class RunResponse(BaseModel):
    output: MatcherAgentOutput
    status: str = "ok"


@app.post("/run", response_model=RunResponse, summary="Run the generic content matcher agent.")
async def run(req: RunRequest) -> RunResponse:
    logger.info(
        "[matcher_agent] run  entity_type=%s  table=%s  execution_id=%s",
        req.input.entity_type,
        req.input.entity_table,
        req.input.execution_id,
    )
    output = await run_matcher_agent(req.input)
    return RunResponse(output=output)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "generic_content_matcher_agent",
        "version": "1.0.0",
        "skills": ["match_content_to_entities", "vector_search_entities"],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agents.templates.matcher_main:app",
        host="0.0.0.0",
        port=8005,
        reload=False,
    )
