"""
Generic Scraper Agent — LangGraph StateGraph.

Nodes
-----
  plan_crawl       — decide which tool/strategy to use based on input
  execute_crawl    — call Scraper MCP (single / batch / deep / discover+batch)
  extract_media    — already done inside fetch_page_full; this node enriches if needed
  normalize_output — apply target_schema via LLM extraction if requested
  deduplicate      — remove pages already seen (by URL hash)
  emit_results     — build final ScraperAgentOutput

This agent is domain-agnostic.  Content curator, HR ingestion, litigation
docket scraping — all call this agent via the Execution Engine.

Call chain:
  domain agent node → Execution Engine → generic_scraper_agent (port 8004)
                                       → Scraper MCP tools (port 8002)
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.core.langfuse_setup import graph_runnable_config, langfuse_trace
from app.core.logging import (
    get_logger,
    log_node_entry,
    log_node_error,
    log_node_exit,
    log_tool_call,
)
from app.guardrails import GuardrailViolation, check_scraping_limits, sanitize_url
from app.domain.policy.models import ScrapingLimits
from agents.templates.scraper_models import (
    NormalizedPage,
    ScraperAgentInput,
    ScraperAgentOutput,
)
from tools.scraper_mcp.client import ScraperMCPClient

logger = get_logger("generic_scraper_agent")


# ── Graph state ───────────────────────────────────────────────────────────────

class ScraperState(TypedDict):
    input: ScraperAgentInput
    scraping_config: dict[str, Any]
    scraping_limits: ScrapingLimits
    plan: dict[str, Any]            # decided strategy + urls
    raw_pages: list[dict[str, Any]] # raw results from MCP
    pages: list[NormalizedPage]     # after normalisation + dedup
    errors: list[str]
    execution_id: str
    tenant_id: str
    t0: float


# ── Node helpers ──────────────────────────────────────────────────────────────

def _scraper_client() -> ScraperMCPClient:
    return ScraperMCPClient(
        base_url=settings.scraper_mcp_url,
        timeout_seconds=settings.scraper_http_timeout_seconds,
    )


def _limits_to_config(limits: ScrapingLimits) -> dict[str, Any]:
    return limits.model_dump()


# ── Node 1: plan_crawl ────────────────────────────────────────────────────────

async def plan_crawl(state: ScraperState) -> ScraperState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="plan_crawl",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    limits = state["scraping_limits"]
    scraping_config = _limits_to_config(limits)

    # Sanitize all input URLs
    valid_urls: list[str] = []
    errors: list[str] = list(state.get("errors", []))
    for url in inp.urls:
        try:
            sanitize_url(url)
            valid_urls.append(url)
        except GuardrailViolation as exc:
            errors.append(f"URL rejected by guardrail: {url} — {exc}")
            logger.warning("[plan_crawl] URL rejected: %s — %s", url[:80], exc)

    # Decide tool strategy
    strategy = inp.strategy
    if strategy == "single" and len(valid_urls) == 1:
        tool = "fetch_page_full"
    elif strategy in ("single", "batch") or (strategy == "bfs" and inp.max_depth == 0):
        tool = "fetch_pages_batch"
    elif strategy in ("bfs", "dfs", "best_first", "adaptive"):
        tool = "deep_crawl"
    else:
        tool = "fetch_pages_batch"

    plan = {
        "tool": tool,
        "urls": valid_urls,
        "strategy": strategy,
        "max_depth": min(inp.max_depth, limits.max_depth),
        "max_pages": min(inp.max_pages, limits.max_total_links),
        "include_media": inp.include_media,
        "include_links": inp.include_links,
    }

    log_node_exit(
        logger, node="plan_crawl", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"tool={tool} urls={len(valid_urls)} strategy={strategy}",
    )
    return {**state, "plan": plan, "scraping_config": scraping_config, "errors": errors}


# ── Node 2: execute_crawl ─────────────────────────────────────────────────────

async def execute_crawl(state: ScraperState) -> ScraperState:
    plan = state["plan"]
    cfg = state["scraping_config"]
    t0 = log_node_entry(
        logger, node="execute_crawl",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        extra={"tool": plan["tool"], "url_count": len(plan["urls"])},
    )

    client = _scraper_client()
    raw_pages: list[dict[str, Any]] = []
    errors: list[str] = list(state.get("errors", []))
    tool_t0 = time.monotonic()

    try:
        tool = plan["tool"]

        if tool == "fetch_page_full" and plan["urls"]:
            result = await client.fetch_page_full(
                url=plan["urls"][0],
                include_media=plan["include_media"],
                include_links=plan["include_links"],
                scraping_config=cfg,
            )
            raw_pages = [result.model_dump()]

        elif tool == "fetch_pages_batch":
            results = await client.fetch_pages_batch(
                urls=plan["urls"],
                include_media=plan["include_media"],
                include_links=plan["include_links"],
                scraping_config=cfg,
            )
            raw_pages = [r.model_dump() for r in results]

        elif tool == "deep_crawl":
            for seed_url in plan["urls"]:
                result = await client.deep_crawl(
                    seed_url=seed_url,
                    strategy=plan["strategy"],
                    max_depth=plan["max_depth"],
                    max_pages=plan["max_pages"],
                    include_media=plan["include_media"],
                    scraping_config=cfg,
                )
                raw_pages.extend([p.model_dump() for p in result.pages])
                if result.error:
                    errors.append(f"deep_crawl error for {seed_url}: {result.error}")

        log_tool_call(
            logger, tool=tool,
            args={"urls": len(plan["urls"]), "strategy": plan["strategy"]},
            result={"pages": len(raw_pages)},
            elapsed_ms=int((time.monotonic() - tool_t0) * 1000),
            execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        )

    except Exception as exc:
        errors.append(f"execute_crawl error: {exc}")
        log_node_error(logger, node="execute_crawl", error=exc, t0=t0,
                       execution_id=state["execution_id"], tenant_id=state["tenant_id"])

    log_node_exit(
        logger, node="execute_crawl", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"fetched {len(raw_pages)} pages",
    )
    return {**state, "raw_pages": raw_pages, "errors": errors}


# ── Node 3: normalize_output ──────────────────────────────────────────────────

async def normalize_output(state: ScraperState) -> ScraperState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="normalize_output",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    pages: list[NormalizedPage] = []
    errors: list[str] = list(state.get("errors", []))

    for raw in state["raw_pages"]:
        if raw.get("error") and not raw.get("clean_text"):
            errors.append(f"Skipping failed page {raw.get('url', '?')}: {raw['error']}")
            continue

        page = NormalizedPage(
            url=raw.get("url", ""),
            title=raw.get("title", ""),
            clean_text=raw.get("clean_text", ""),
            depth=raw.get("depth", 0),
            parent_url=raw.get("parent_url", ""),
            metadata=raw.get("metadata", {}),
            images=raw.get("images", []),
            videos=raw.get("videos", []),
            audio=raw.get("audio", []),
            links=raw.get("links", {}),
            status_code=raw.get("status_code", 200),
            error=raw.get("error"),
        )

        # LLM-based schema normalisation if target_schema provided
        if inp.target_schema and page.clean_text:
            try:
                llm = ChatOpenAI(
                    model=settings.openai_model,
                    api_key=settings.openai_api_key,
                    temperature=0,
                )
                schema_str = str(inp.target_schema)
                prompt = (
                    f"Extract structured data from the following web page content "
                    f"and return valid JSON matching this schema:\n{schema_str}\n\n"
                    f"Page content:\n{page.clean_text[:4000]}\n\n"
                    f"Return only the JSON object, no explanation."
                )
                import json as _json
                response = await llm.ainvoke(prompt, config=graph_runnable_config())
                try:
                    page.structured_data = _json.loads(response.content)
                except Exception:
                    page.structured_data = response.content
            except Exception as exc:
                errors.append(f"LLM normalisation failed for {page.url}: {exc}")
                logger.warning("[normalize_output] LLM error for %s: %s", page.url[:80], exc)

        pages.append(page)

    log_node_exit(
        logger, node="normalize_output", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"normalised {len(pages)} pages",
    )
    return {**state, "pages": pages, "errors": errors}


# ── Node 4: deduplicate ───────────────────────────────────────────────────────

async def deduplicate(state: ScraperState) -> ScraperState:
    t0 = log_node_entry(
        logger, node="deduplicate",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    seen: set[str] = set()
    unique: list[NormalizedPage] = []
    for page in state["pages"]:
        key = page.url.rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(page)

    removed = len(state["pages"]) - len(unique)
    log_node_exit(
        logger, node="deduplicate", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"kept {len(unique)}, removed {removed} duplicates",
    )
    return {**state, "pages": unique}


# ── Node 5: emit_results ──────────────────────────────────────────────────────

async def emit_results(state: ScraperState) -> ScraperState:
    t0 = log_node_entry(
        logger, node="emit_results",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )
    log_node_exit(
        logger, node="emit_results", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=(
            f"total={len(state['pages'])} "
            f"errors={len(state.get('errors', []))}"
        ),
    )
    return state


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_scraper_graph() -> Any:
    graph = StateGraph(ScraperState)

    graph.add_node("plan_crawl", plan_crawl)
    graph.add_node("execute_crawl", execute_crawl)
    graph.add_node("normalize_output", normalize_output)
    graph.add_node("deduplicate", deduplicate)
    graph.add_node("emit_results", emit_results)

    graph.set_entry_point("plan_crawl")
    graph.add_edge("plan_crawl", "execute_crawl")
    graph.add_edge("execute_crawl", "normalize_output")
    graph.add_edge("normalize_output", "deduplicate")
    graph.add_edge("deduplicate", "emit_results")
    graph.add_edge("emit_results", END)

    return graph.compile()


# ── Public run function ───────────────────────────────────────────────────────

async def run_scraper_agent(
    agent_input: ScraperAgentInput,
) -> ScraperAgentOutput:
    """
    Entry point called by the FastAPI wrapper and by other agents.
    """
    from app.domain.policy.models import ScrapingLimits as _Limits

    t0 = time.monotonic()
    limits = _Limits(**agent_input.scraping_limits.model_dump())

    initial_state: ScraperState = {
        "input": agent_input,
        "scraping_config": limits.model_dump(),
        "scraping_limits": limits,
        "plan": {},
        "raw_pages": [],
        "pages": [],
        "errors": [],
        "execution_id": agent_input.execution_id,
        "tenant_id": agent_input.tenant_id,
        "t0": t0,
    }

    graph = build_scraper_graph()
    with langfuse_trace(
        tenant_id=agent_input.tenant_id or "unknown",
        execution_id=agent_input.execution_id or "unknown",
        service="generic_scraper",
        skill_id="generic_scrape",
    ) as lf_cfg:
        final_state: ScraperState = await graph.ainvoke(initial_state, config=lf_cfg)

    total = len(final_state["pages"])
    failed = len([p for p in final_state["pages"] if p.error])

    return ScraperAgentOutput(
        pages=final_state["pages"],
        total_scraped=total,
        total_failed=failed,
        deduplicated=len(final_state["raw_pages"]) - total,
        execution_id=agent_input.execution_id,
        duration_ms=int((time.monotonic() - t0) * 1000),
        errors=final_state.get("errors", []),
    )
