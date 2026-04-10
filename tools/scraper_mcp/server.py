"""
Scraper MCP Service — FastAPI app (port 8002).

Backend: crawl4ai + Playwright (handles SSR, CSR, SPA, JS-heavy pages).
Fallback: requests + trafilatura for static pages when crawl4ai is unavailable.

Endpoints
---------
  POST /tools/fetch_page              — single page, text only (existing, extended)
  POST /tools/fetch_page_full         — text + media + links + optional screenshot
  POST /tools/fetch_pages_batch       — parallel multi-URL crawl
  POST /tools/fetch_links             — links from one page (existing, extended)
  POST /tools/discover_urls           — multi-depth URL discovery (fast, no content)
  POST /tools/deep_crawl              — full deep crawl (BFS/DFS/BestFirst/Adaptive)
  POST /tools/screenshot_page         — viewport screenshot → base64
  POST /tools/extract_structured      — LLM-based JSON extraction
  POST /tools/extract_structured_no_llm — CSS/XPath schema extraction
  GET  /health

All endpoints accept a `scraping_config` block carrying tenant policy limits.
Guardrails enforce limits before every crawl.

To run:
  uv run uvicorn tools.scraper_mcp.server:app --port 8002 --reload
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.logging import get_logger, log_tool_call
from app.domain.policy.models import ScrapingLimits
from app.guardrails import (
    GuardrailViolation,
    check_scraping_limits,
    check_tool_allowed,
    sanitize_url,
)
from app.domain.policy.models import EffectivePolicy
from tools.scraper_mcp.helpers import (
    deep_crawl_async,
    discover_urls_async,
    extract_structured_async,
    extract_structured_no_llm_async,
    fetch_links_async,
    fetch_page_async,
    fetch_page_full_async,
    fetch_pages_batch_async,
    init_shared_crawler,
    screenshot_page_async,
    shutdown_shared_crawler,
)

logger = get_logger("scraper_mcp")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await init_shared_crawler()
    yield
    await shutdown_shared_crawler()


app = FastAPI(
    title="Scraper MCP Service",
    version="2.0.0",
    description="9 crawl4ai-backed tools for web scraping, deep crawling, media extraction, and structured data extraction.",
    lifespan=_lifespan,
)


# ── Shared scraping_config model ──────────────────────────────────────────────

class ScrapingConfig(BaseModel):
    """Tenant policy limits passed with every tool request."""

    max_depth: int = Field(2, ge=0, le=10)
    max_links_per_page: int = Field(30, ge=1, le=500)
    max_total_links: int = Field(100, ge=1, le=10_000)
    allow_external_domains: bool = False
    allow_subdomains: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = Field(3, ge=1, le=20)
    request_delay_ms: int = Field(500, ge=0)

    def to_scraping_limits(self) -> ScrapingLimits:
        return ScrapingLimits(**self.model_dump())


def _default_config() -> ScrapingConfig:
    return ScrapingConfig()


def _guard_url(url: str, tool_id: str, cfg: ScrapingConfig) -> None:
    """Run URL sanitisation and tool-level guardrails."""
    try:
        sanitize_url(url)
    except GuardrailViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    limits = cfg.to_scraping_limits()
    # Build a minimal EffectivePolicy for tool-level check
    ep = EffectivePolicy(raw={"security": {"allowWebScraping": True}}, scraping_limits=limits)
    try:
        check_tool_allowed(tool_id, ep)
    except GuardrailViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# ── 1. fetch_page ─────────────────────────────────────────────────────────────

class FetchPageRequest(BaseModel):
    url: str
    last_content_hash: str | None = None
    wait_for: str | None = None
    js_code: str | None = None
    session_id: str | None = None
    scroll_to_bottom: bool = False
    stealth_mode: bool = False
    proxy: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class FetchPageResponse(BaseModel):
    url: str
    status_code: int
    changed: bool
    raw_html: str
    clean_text: str
    title: str
    etag: str | None = None
    last_modified_header: str | None = None
    content_hash: str
    duration_ms: int
    error: str | None = None


@app.post("/tools/fetch_page", response_model=FetchPageResponse,
          summary="Fetch a single page (text only). Handles SSR/CSR/SPA/JS-heavy.")
async def fetch_page(req: FetchPageRequest) -> FetchPageResponse:
    _guard_url(req.url, "fetch_page", req.scraping_config)
    t0 = time.monotonic()
    result = await fetch_page_async(
        url=req.url,
        last_content_hash=req.last_content_hash,
        wait_for=req.wait_for,
        js_code=req.js_code,
        session_id=req.session_id,
        scroll_to_bottom=req.scroll_to_bottom,
        stealth_mode=req.stealth_mode,
        proxy=req.proxy,
    )
    log_tool_call(logger, tool="fetch_page", args={"url": req.url},
                  result={"changed": result.get("changed"), "title": result.get("title")},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return FetchPageResponse(**result)


# ── 2. fetch_page_full ────────────────────────────────────────────────────────

class FetchPageFullRequest(BaseModel):
    url: str
    include_media: bool = True
    include_links: bool = True
    include_raw_html: bool = False
    screenshot: bool = False
    wait_for: str | None = None
    js_code: str | None = None
    session_id: str | None = None
    scroll_to_bottom: bool = False
    stealth_mode: bool = False
    proxy: str | None = None
    last_content_hash: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class MediaItem(BaseModel):
    """Crawl4ai sometimes emits null alt/type — coerce so responses always validate."""

    src: str = ""
    alt: str = ""
    score: float = 0.0
    type: str = ""

    @field_validator("src", "alt", "type", mode="before")
    @classmethod
    def _coerce_str_fields(cls, v: object) -> str:
        return "" if v is None else str(v)

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v: object) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0


class LinkItem(BaseModel):
    href: str = ""
    text: str = ""

    @field_validator("href", "text", mode="before")
    @classmethod
    def _coerce_link_str(cls, v: object) -> str:
        return "" if v is None else str(v)


class FetchPageFullResponse(BaseModel):
    url: str
    status_code: int
    changed: bool
    clean_text: str
    raw_html: str = ""
    title: str
    content_hash: str
    duration_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    images: list[MediaItem] = Field(default_factory=list)
    videos: list[MediaItem] = Field(default_factory=list)
    audio: list[MediaItem] = Field(default_factory=list)
    links: dict[str, list[LinkItem]] = Field(default_factory=dict)
    screenshot_base64: str | None = None
    error: str | None = None


@app.post("/tools/fetch_page_full", response_model=FetchPageFullResponse,
          summary="Full page: text + media (images/videos/audio) + links + optional screenshot.")
async def fetch_page_full(req: FetchPageFullRequest) -> FetchPageFullResponse:
    _guard_url(req.url, "fetch_page_full", req.scraping_config)
    t0 = time.monotonic()
    result = await fetch_page_full_async(
        url=req.url,
        include_media=req.include_media,
        include_links=req.include_links,
        include_raw_html=req.include_raw_html,
        screenshot=req.screenshot,
        wait_for=req.wait_for,
        js_code=req.js_code,
        session_id=req.session_id,
        scroll_to_bottom=req.scroll_to_bottom,
        stealth_mode=req.stealth_mode,
        proxy=req.proxy,
        last_content_hash=req.last_content_hash,
    )
    log_tool_call(
        logger, tool="fetch_page_full", args={"url": req.url, "include_media": req.include_media},
        result={
            "images": len(result.get("images", [])),
            "videos": len(result.get("videos", [])),
            "internal_links": len(result.get("links", {}).get("internal", [])),
        },
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        error=result.get("error"),
    )
    return FetchPageFullResponse(**result)


# ── 3. fetch_pages_batch ──────────────────────────────────────────────────────

class FetchPagesBatchRequest(BaseModel):
    urls: list[str]
    include_media: bool = False
    include_links: bool = False
    wait_for: str | None = None
    js_code: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class FetchPagesBatchResponse(BaseModel):
    pages: list[FetchPageFullResponse]
    total: int
    duration_ms: int


@app.post("/tools/fetch_pages_batch", response_model=FetchPagesBatchResponse,
          summary="Parallel crawl of multiple URLs. Returns one result per URL.")
async def fetch_pages_batch(req: FetchPagesBatchRequest) -> FetchPagesBatchResponse:
    for url in req.urls:
        _guard_url(url, "fetch_pages_batch", req.scraping_config)

    t0 = time.monotonic()
    results = await fetch_pages_batch_async(
        urls=req.urls,
        include_media=req.include_media,
        include_links=req.include_links,
        max_concurrent=req.scraping_config.max_concurrent_requests,
        wait_for=req.wait_for,
        js_code=req.js_code,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    log_tool_call(logger, tool="fetch_pages_batch",
                  args={"url_count": len(req.urls)},
                  result={"pages": len(results)},
                  elapsed_ms=elapsed)
    return FetchPagesBatchResponse(pages=results, total=len(results), duration_ms=elapsed)


# ── 4. fetch_links ────────────────────────────────────────────────────────────

class FetchLinksRequest(BaseModel):
    url: str
    same_domain_only: bool = True
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    wait_for: str | None = None
    session_id: str | None = None
    max_links: int = Field(200, ge=1, le=2000)
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class FetchLinksResponse(BaseModel):
    url: str
    links: list[LinkItem]
    status_code: int
    duration_ms: int
    error: str | None = None


@app.post("/tools/fetch_links", response_model=FetchLinksResponse,
          summary="Extract all links from a page with optional pattern filtering.")
async def fetch_links(req: FetchLinksRequest) -> FetchLinksResponse:
    _guard_url(req.url, "fetch_links", req.scraping_config)
    t0 = time.monotonic()
    effective_max = min(req.max_links, req.scraping_config.max_links_per_page)
    result = await fetch_links_async(
        url=req.url,
        same_domain_only=req.same_domain_only,
        include_patterns=req.include_patterns or None,
        exclude_patterns=req.exclude_patterns or None,
        wait_for=req.wait_for,
        session_id=req.session_id,
        max_links=effective_max,
    )
    log_tool_call(logger, tool="fetch_links", args={"url": req.url},
                  result={"links": len(result.get("links", []))},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return FetchLinksResponse(**result)


# ── 5. discover_urls ──────────────────────────────────────────────────────────

class DiscoverUrlsRequest(BaseModel):
    seed_url: str
    max_depth: int = Field(2, ge=0, le=10)
    max_total_urls: int = Field(100, ge=1, le=5000)
    same_domain_only: bool = True
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class DiscoveredUrl(BaseModel):
    url: str
    depth: int
    parent_url: str = ""


class DiscoverUrlsResponse(BaseModel):
    seed_url: str
    urls: list[DiscoveredUrl]
    total: int
    duration_ms: int
    error: str | None = None


@app.post("/tools/discover_urls", response_model=DiscoverUrlsResponse,
          summary="Fast multi-depth URL discovery (no content extraction, 5-10x faster).")
async def discover_urls(req: DiscoverUrlsRequest) -> DiscoverUrlsResponse:
    _guard_url(req.seed_url, "discover_urls", req.scraping_config)
    limits = req.scraping_config.to_scraping_limits()
    effective_depth = min(req.max_depth, limits.max_depth)
    effective_total = min(req.max_total_urls, limits.max_total_links)

    t0 = time.monotonic()
    result = await discover_urls_async(
        seed_url=req.seed_url,
        max_depth=effective_depth,
        max_total_urls=effective_total,
        same_domain_only=req.same_domain_only,
        include_patterns=req.include_patterns or None,
        exclude_patterns=req.exclude_patterns or None,
    )
    log_tool_call(logger, tool="discover_urls", args={"seed_url": req.seed_url, "max_depth": effective_depth},
                  result={"total": result.get("total")},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return DiscoverUrlsResponse(**result)


# ── 6. deep_crawl ─────────────────────────────────────────────────────────────

class DeepCrawlRequest(BaseModel):
    seed_url: str
    strategy: str = Field("bfs", pattern="^(bfs|dfs|best_first|adaptive)$")
    max_depth: int = Field(2, ge=0, le=10)
    max_pages: int = Field(50, ge=1, le=1000)
    include_external: bool = False
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    include_media: bool = False
    query: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class DeepCrawlPage(BaseModel):
    url: str
    depth: int
    parent_url: str = ""
    clean_text: str
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    images: list[MediaItem] = Field(default_factory=list)
    videos: list[MediaItem] = Field(default_factory=list)
    audio: list[MediaItem] = Field(default_factory=list)
    status_code: int
    error: str | None = None


class DeepCrawlResponse(BaseModel):
    seed_url: str
    strategy: str
    pages: list[DeepCrawlPage]
    total: int
    duration_ms: int
    error: str | None = None


@app.post("/tools/deep_crawl", response_model=DeepCrawlResponse,
          summary="Deep crawl with BFS/DFS/BestFirst/Adaptive strategy. Returns full page content.")
async def deep_crawl(req: DeepCrawlRequest) -> DeepCrawlResponse:
    _guard_url(req.seed_url, "deep_crawl", req.scraping_config)
    limits = req.scraping_config.to_scraping_limits()
    effective_depth = min(req.max_depth, limits.max_depth)
    effective_pages = min(req.max_pages, limits.max_total_links)

    t0 = time.monotonic()
    result = await deep_crawl_async(
        seed_url=req.seed_url,
        strategy=req.strategy,
        max_depth=effective_depth,
        max_pages=effective_pages,
        include_external=req.include_external and limits.allow_external_domains,
        include_patterns=req.include_patterns or None,
        exclude_patterns=req.exclude_patterns or None,
        include_media=req.include_media,
        query=req.query,
    )
    log_tool_call(
        logger, tool="deep_crawl",
        args={"seed_url": req.seed_url, "strategy": req.strategy,
              "max_depth": effective_depth, "max_pages": effective_pages},
        result={"total": result.get("total")},
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        error=result.get("error"),
    )
    return DeepCrawlResponse(**result)


# ── 7. screenshot_page ────────────────────────────────────────────────────────

class ScreenshotRequest(BaseModel):
    url: str
    wait_for: str | None = None
    full_page: bool = False
    js_code: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class ScreenshotResponse(BaseModel):
    url: str
    screenshot_base64: str | None
    width: int
    height: int
    duration_ms: int
    error: str | None = None


@app.post("/tools/screenshot_page", response_model=ScreenshotResponse,
          summary="Capture a viewport screenshot (base64 PNG). Useful for visual QA and multimodal LLMs.")
async def screenshot_page(req: ScreenshotRequest) -> ScreenshotResponse:
    _guard_url(req.url, "screenshot_page", req.scraping_config)
    t0 = time.monotonic()
    result = await screenshot_page_async(
        url=req.url,
        wait_for=req.wait_for,
        full_page=req.full_page,
        js_code=req.js_code,
    )
    log_tool_call(logger, tool="screenshot_page", args={"url": req.url},
                  result={"has_screenshot": result.get("screenshot_base64") is not None},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return ScreenshotResponse(**result)


# ── 8. extract_structured ─────────────────────────────────────────────────────

class ExtractStructuredRequest(BaseModel):
    url: str
    schema_json: dict[str, Any]
    wait_for: str | None = None
    js_code: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class ExtractStructuredResponse(BaseModel):
    url: str
    data: Any
    duration_ms: int
    error: str | None = None


@app.post("/tools/extract_structured", response_model=ExtractStructuredResponse,
          summary="LLM-based structured JSON extraction against a caller-supplied schema.")
async def extract_structured(req: ExtractStructuredRequest) -> ExtractStructuredResponse:
    _guard_url(req.url, "extract_structured", req.scraping_config)
    t0 = time.monotonic()
    result = await extract_structured_async(
        url=req.url,
        schema_json=req.schema_json,
        wait_for=req.wait_for,
        js_code=req.js_code,
    )
    log_tool_call(logger, tool="extract_structured", args={"url": req.url},
                  result={"has_data": result.get("data") is not None},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return ExtractStructuredResponse(**result)


# ── 9. extract_structured_no_llm ─────────────────────────────────────────────

class ExtractNoLLMRequest(BaseModel):
    url: str
    extraction_schema: dict[str, Any]
    wait_for: str | None = None
    js_code: str | None = None
    scraping_config: ScrapingConfig = Field(default_factory=_default_config)


class ExtractNoLLMResponse(BaseModel):
    url: str
    data: Any
    duration_ms: int
    error: str | None = None


@app.post("/tools/extract_structured_no_llm", response_model=ExtractNoLLMResponse,
          summary="CSS/XPath schema extraction — no LLM, fast and cheap for well-structured pages.")
async def extract_structured_no_llm(req: ExtractNoLLMRequest) -> ExtractNoLLMResponse:
    _guard_url(req.url, "extract_structured_no_llm", req.scraping_config)
    t0 = time.monotonic()
    result = await extract_structured_no_llm_async(
        url=req.url,
        extraction_schema=req.extraction_schema,
        wait_for=req.wait_for,
        js_code=req.js_code,
    )
    log_tool_call(logger, tool="extract_structured_no_llm", args={"url": req.url},
                  result={"has_data": result.get("data") is not None},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return ExtractNoLLMResponse(**result)


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "scraper-mcp",
        "version": "2.0.0",
        "backend": "crawl4ai+playwright",
        "tools": [
            "fetch_page", "fetch_page_full", "fetch_pages_batch",
            "fetch_links", "discover_urls", "deep_crawl",
            "screenshot_page", "extract_structured", "extract_structured_no_llm",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "tools.scraper_mcp.server:app",
        host="0.0.0.0",
        port=settings.scraper_mcp_port,
        reload=False,
    )
