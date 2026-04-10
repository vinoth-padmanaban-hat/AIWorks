"""Pydantic I/O models for the Generic Scraper Agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScrapingLimitsInput(BaseModel):
    max_depth: int = 2
    max_links_per_page: int = 30
    max_total_links: int = 100
    allow_external_domains: bool = False
    allow_subdomains: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = 3
    request_delay_ms: int = 500


class ScraperAgentInput(BaseModel):
    """Input to the generic scraper agent."""

    # At least one of urls or search_queries must be provided.
    urls: list[str] = Field(default_factory=list, description="Seed URLs to crawl.")
    search_queries: list[str] = Field(
        default_factory=list,
        description="Web search queries — results are crawled after searching.",
    )

    # Crawl behaviour
    strategy: str = Field(
        "bfs",
        pattern="^(single|batch|bfs|dfs|best_first|adaptive)$",
        description=(
            "single   — fetch each URL once (no deep crawl).\n"
            "batch    — parallel fetch of all URLs.\n"
            "bfs/dfs/best_first/adaptive — deep crawl from each seed URL."
        ),
    )
    max_depth: int = Field(2, ge=0, le=10)
    max_pages: int = Field(50, ge=1, le=1000)
    include_media: bool = Field(False, description="Extract images/videos/audio.")
    include_links: bool = Field(False, description="Include discovered links in output.")

    # Schema for normalisation (optional)
    target_schema: dict[str, Any] | None = Field(
        None,
        description=(
            "If provided, each scraped page is normalised to this JSON schema "
            "using an LLM extraction step."
        ),
    )

    # Tenant policy limits (injected by orchestrator / calling agent)
    scraping_limits: ScrapingLimitsInput = Field(default_factory=ScrapingLimitsInput)

    # Tracing context
    execution_id: str = ""
    tenant_id: str = ""
    step_id: str = ""


class NormalizedPage(BaseModel):
    """One scraped and normalised page."""

    url: str
    title: str = ""
    clean_text: str = ""
    depth: int = 0
    parent_url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    images: list[dict[str, Any]] = Field(default_factory=list)
    videos: list[dict[str, Any]] = Field(default_factory=list)
    audio: list[dict[str, Any]] = Field(default_factory=list)
    links: dict[str, list[dict]] = Field(default_factory=dict)
    structured_data: Any = None   # populated when target_schema is provided
    status_code: int = 200
    error: str | None = None


class ScraperAgentOutput(BaseModel):
    """Output from the generic scraper agent."""

    pages: list[NormalizedPage] = Field(default_factory=list)
    total_scraped: int = 0
    total_failed: int = 0
    deduplicated: int = 0
    execution_id: str = ""
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)
