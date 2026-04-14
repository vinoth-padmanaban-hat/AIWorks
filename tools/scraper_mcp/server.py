"""
Scraper MCP service (FastMCP, streamable HTTP).

Purpose
-------
Exposes web acquisition and extraction to agents and the control plane over MCP.
Clients connect to ``host:port/mcp`` (see ``settings.scraper_mcp_port``; default 8002).
Each tool accepts a ``scraping_config`` block (depth, link caps, domains, concurrency,
rate limits) so tenant policy can bound cost and scope. URLs are sanitized and checked
against tool allowlists before work runs.

Runtime
-------
Primary engine: **crawl4ai** with **Playwright** for JS-heavy and SPAs. Helpers may fall
back to **requests** + **trafilatura** when a browser crawl is unavailable. A shared
crawler is initialized at process startup (see lifespan) and torn down on shutdown.

Tools exposed (MCP)
--------------------
**Fetch & render**
    ``fetch_page`` — Single URL: HTML, clean text, title, hashes, change detection.
    ``fetch_page_full`` — Same plus optional media (images/video/audio), links, raw HTML,
        viewport screenshot (base64).
    ``fetch_pages_batch`` — Parallel multi-URL fetch (full-page style results per URL).

**Links & discovery**
    ``fetch_links`` — Outbound links from one page (patterns, same-domain filter).
    ``discover_urls`` — Breadth-style URL discovery from a seed (depth/total caps, patterns).
    ``extract_links`` — Thin wrapper over ``fetch_links`` with a simplified input model.

**Crawl**
    ``deep_crawl`` — Multi-page crawl (BFS/DFS/best_first/adaptive), optional media,
        optional query hint for strategies that support it.
    ``crawl_url`` — Convenience: ``max_depth`` > 0 delegates to ``deep_crawl``; else
        ``fetch_page_full``.

**Search**
    ``search_and_crawl`` — Resolves a text query to URLs (DuckDuckGo HTML scrape), then
        batch-fetches those pages.

**Screenshots**
    ``screenshot_page`` — Viewport or full-page PNG as base64.

**Structured extraction**
    ``extract_structured`` — LLM-backed JSON extraction from a page given a JSON schema.
    ``extract_structured_no_llm`` — **JsonCssExtractionStrategy** (CSS selectors); supports
        crawl4ai native schema or shorthand field→selector maps (see helpers).

**Media**
    ``extract_media`` — Images, video, and audio references from a page (via ``fetch_page_full``).

**Other**
    ``normalize_to_schema`` — Placeholder: returns an error pointing callers to
        ``extract_structured`` for URL-based normalization.

Backend: crawl4ai + Playwright (handles SSR, CSR, SPA, JS-heavy pages).
Fallback: requests + trafilatura for static pages when crawl4ai is unavailable.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
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


@lifespan
async def _server_lifespan(_server: FastMCP):
    await init_shared_crawler()
    yield
    await shutdown_shared_crawler()


mcp = FastMCP(
    name="scraper-mcp",
    instructions="crawl4ai-backed scraping, crawling, and extraction tools.",
    lifespan=_server_lifespan,
)


# ── Shared scraping_config model ──────────────────────────────────────────────

class ScrapingConfig(BaseModel):
    """
    Tenant policy limits passed with every tool request.
    Defaults are intentionally conservative for shared infrastructure:
    """

    max_depth: int = Field(1, ge=0, le=4)
    max_links_per_page: int = Field(12, ge=1, le=100)
    max_total_links: int = Field(40, ge=1, le=500)
    allow_external_domains: bool = False
    allow_subdomains: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = Field(2, ge=1, le=8)
    request_delay_ms: int = Field(750, ge=100, le=10_000)

    def to_scraping_limits(self) -> ScrapingLimits:
        return ScrapingLimits(**self.model_dump())


def _default_config() -> ScrapingConfig:
    return ScrapingConfig()


def _guard_url(url: str, tool_id: str, cfg: ScrapingConfig) -> str | None:
    """Run URL sanitisation and tool-level guardrails."""
    try:
        sanitize_url(url)
    except GuardrailViolation as exc:
        return str(exc)

    limits = cfg.to_scraping_limits()
    ep = EffectivePolicy(raw={"security": {"allowWebScraping": True}}, scraping_limits=limits)
    try:
        check_tool_allowed(tool_id, ep)
    except GuardrailViolation as exc:
        return str(exc)
    return None


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


@mcp.tool()
async def fetch_page(req: FetchPageRequest) -> FetchPageResponse:
    violation = _guard_url(req.url, "fetch_page", req.scraping_config)
    if violation:
        return FetchPageResponse(
            url=req.url, status_code=0, changed=False, raw_html="", clean_text="", title="",
            content_hash="", duration_ms=0, error=violation
        )
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
        scraping_config=req.scraping_config.model_dump(),
    )
    log_tool_call(logger, tool="fetch_page", args={"url": req.url},
                  result={"changed": result.get("changed"), "title": result.get("title")},
                  elapsed_ms=int((time.monotonic() - t0) * 1000),
                  error=result.get("error"))
    return FetchPageResponse.model_validate(result)


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


@mcp.tool()
async def fetch_page_full(req: FetchPageFullRequest) -> FetchPageFullResponse:
    violation = _guard_url(req.url, "fetch_page_full", req.scraping_config)
    if violation:
        return FetchPageFullResponse(
            url=req.url, status_code=0, changed=False, clean_text="", title="",
            content_hash="", duration_ms=0, error=violation
        )
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
        scraping_config=req.scraping_config.model_dump(),
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
    return FetchPageFullResponse.model_validate(result)


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


@mcp.tool()
async def fetch_pages_batch(req: FetchPagesBatchRequest) -> FetchPagesBatchResponse:
    for url in req.urls:
        violation = _guard_url(url, "fetch_pages_batch", req.scraping_config)
        if violation:
            return FetchPagesBatchResponse(pages=[], total=0, duration_ms=0)

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


@mcp.tool()
async def fetch_links(req: FetchLinksRequest) -> FetchLinksResponse:
    violation = _guard_url(req.url, "fetch_links", req.scraping_config)
    if violation:
        return FetchLinksResponse(
            url=req.url, links=[], status_code=0, duration_ms=0, error=violation
        )
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
    return FetchLinksResponse.model_validate(result)


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


@mcp.tool()
async def discover_urls(req: DiscoverUrlsRequest) -> DiscoverUrlsResponse:
    violation = _guard_url(req.seed_url, "discover_urls", req.scraping_config)
    if violation:
        return DiscoverUrlsResponse(
            seed_url=req.seed_url, urls=[], total=0, duration_ms=0, error=violation
        )
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
    return DiscoverUrlsResponse.model_validate(result)


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


@mcp.tool()
async def deep_crawl(req: DeepCrawlRequest) -> DeepCrawlResponse:
    violation = _guard_url(req.seed_url, "deep_crawl", req.scraping_config)
    if violation:
        return DeepCrawlResponse(
            seed_url=req.seed_url, strategy=req.strategy, pages=[], total=0, duration_ms=0, error=violation
        )
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
    return DeepCrawlResponse.model_validate(result)


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


@mcp.tool()
async def screenshot_page(req: ScreenshotRequest) -> ScreenshotResponse:
    violation = _guard_url(req.url, "screenshot_page", req.scraping_config)
    if violation:
        return ScreenshotResponse(
            url=req.url, screenshot_base64=None, width=0, height=0, duration_ms=0, error=violation
        )
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
    return ScreenshotResponse.model_validate(result)


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


@mcp.tool()
async def extract_structured(req: ExtractStructuredRequest) -> ExtractStructuredResponse:
    violation = _guard_url(req.url, "extract_structured", req.scraping_config)
    if violation:
        return ExtractStructuredResponse(url=req.url, data=None, duration_ms=0, error=violation)
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
    return ExtractStructuredResponse.model_validate(result)


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


@mcp.tool()
async def extract_structured_no_llm(req: ExtractNoLLMRequest) -> ExtractNoLLMResponse:
    violation = _guard_url(req.url, "extract_structured_no_llm", req.scraping_config)
    if violation:
        return ExtractNoLLMResponse(url=req.url, data=None, duration_ms=0, error=violation)
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
    return ExtractNoLLMResponse.model_validate(result)


class CrawlUrlInput(BaseModel):
    url: str
    config: ScrapingConfig = Field(default_factory=_default_config)
    include_media: bool = True
    include_links: bool = True
    max_depth: int = 0
    max_pages: int = 20
    strategy: str = "bfs"


@mcp.tool()
async def crawl_url(req: CrawlUrlInput) -> dict[str, Any]:
    if req.max_depth > 0:
        out = await deep_crawl(DeepCrawlRequest(
            seed_url=req.url,
            strategy=req.strategy,
            max_depth=req.max_depth,
            max_pages=req.max_pages,
            include_media=req.include_media,
            scraping_config=req.config,
        ))
        return out.model_dump()
    out = await fetch_page_full(FetchPageFullRequest(
        url=req.url,
        include_media=req.include_media,
        include_links=req.include_links,
        scraping_config=req.config,
    ))
    return out.model_dump()


class SearchAndCrawlInput(BaseModel):
    query: str
    max_results: int = Field(default=5, ge=1, le=20)
    config: ScrapingConfig = Field(default_factory=_default_config)
    include_media: bool = False
    include_links: bool = True


async def _search_urls(query: str, max_results: int) -> list[str]:
    if query.startswith("http://") or query.startswith("https://"):
        return [query]
    q = query.strip()
    if not q:
        return []
    try:
        url = "https://duckduckgo.com/html/"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
            res = await http.get(url, params={"q": q})
            res.raise_for_status()
        hrefs = []
        for token in res.text.split('href="'):
            if token.startswith("http"):
                hrefs.append(token.split('"', 1)[0])
        deduped: list[str] = []
        seen: set[str] = set()
        for h in hrefs:
            if h not in seen:
                seen.add(h)
                deduped.append(h)
            if len(deduped) >= max_results:
                break
        return deduped
    except Exception:
        return []


@mcp.tool()
async def search_and_crawl(req: SearchAndCrawlInput) -> dict[str, Any]:
    urls = await _search_urls(req.query, req.max_results)
    if not urls:
        return {"query": req.query, "pages": [], "total": 0, "error": "No search results found."}
    out = await fetch_pages_batch(FetchPagesBatchRequest(
        urls=urls,
        include_media=req.include_media,
        include_links=req.include_links,
        scraping_config=req.config,
    ))
    return {"query": req.query, "pages": [p.model_dump() for p in out.pages], "total": out.total, "error": None}


class ExtractLinksInput(BaseModel):
    url: str
    config: ScrapingConfig = Field(default_factory=_default_config)
    same_domain_only: bool = True
    max_links: int = 200


@mcp.tool()
async def extract_links(req: ExtractLinksInput) -> dict[str, Any]:
    out = await fetch_links(FetchLinksRequest(
        url=req.url,
        same_domain_only=req.same_domain_only,
        max_links=req.max_links,
        scraping_config=req.config,
    ))
    return out.model_dump()


class ExtractMediaInput(BaseModel):
    url: str
    config: ScrapingConfig = Field(default_factory=_default_config)


@mcp.tool()
async def extract_media(req: ExtractMediaInput) -> dict[str, Any]:
    out = await fetch_page_full(FetchPageFullRequest(
        url=req.url,
        include_media=True,
        include_links=False,
        include_raw_html=False,
        scraping_config=req.config,
    ))
    return {
        "url": out.url,
        "images": [m.model_dump() for m in out.images],
        "videos": [m.model_dump() for m in out.videos],
        "audio": [m.model_dump() for m in out.audio],
        "duration_ms": out.duration_ms,
        "error": out.error,
    }


class NormalizeToSchemaInput(BaseModel):
    raw_content: str
    target_schema: dict[str, Any]


@mcp.tool()
async def normalize_to_schema(req: NormalizeToSchemaInput) -> dict[str, Any]:
    return {
        "raw_content": req.raw_content,
        "target_schema": req.target_schema,
        "normalized": None,
        "error": "Use extract_structured with a URL for crawl4ai-backed normalization.",
    }


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=settings.scraper_mcp_port,
        path="/mcp",
    )
