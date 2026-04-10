"""
Generic Scraper Agent — FastAPI service (port 8004).

Wraps scraper_graph.py as an HTTP service so the Execution Engine can
dispatch to it via the Agent Registry.

To run:
  uv run uvicorn agents.templates.scraper_main:app --port 8004 --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import settings
from app.core.langfuse_setup import flush_langfuse, init_langfuse
from app.core.logging import get_logger
from agents.templates.scraper_graph import run_scraper_agent
from agents.templates.scraper_models import ScraperAgentInput, ScraperAgentOutput

logger = get_logger("generic_scraper_agent")

app = FastAPI(
    title="Generic Scraper Agent",
    version="1.0.0",
    description=(
        "Reusable web acquisition agent. "
        "Supports single-page, batch, and deep crawl (BFS/DFS/BestFirst/Adaptive). "
        "Enforces tenant scraping limits. "
        "Optionally normalises output to a caller-supplied JSON schema."
    ),
)


@app.on_event("startup")
async def _langfuse_startup() -> None:
    init_langfuse()


@app.on_event("shutdown")
async def _langfuse_shutdown() -> None:
    flush_langfuse()


class RunRequest(BaseModel):
    input: ScraperAgentInput


class RunResponse(BaseModel):
    output: ScraperAgentOutput
    status: str = "ok"


@app.post("/run", response_model=RunResponse, summary="Run the generic scraper agent.")
async def run(req: RunRequest) -> RunResponse:
    logger.info(
        "[scraper_agent] run  urls=%d  queries=%d  strategy=%s  execution_id=%s",
        len(req.input.urls),
        len(req.input.search_queries),
        req.input.strategy,
        req.input.execution_id,
    )
    output = await run_scraper_agent(req.input)
    return RunResponse(output=output)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "generic_scraper_agent",
        "version": "1.0.0",
        "skills": ["scrape_urls", "search_and_scrape", "extract_media_from_url"],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agents.templates.scraper_main:app",
        host="0.0.0.0",
        port=8004,
        reload=False,
    )
