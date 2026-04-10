"""
Pydantic I/O models for each skill implemented by the content ingestion agent.
These are the skill schemas — implementation-agnostic boundary types.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Skill: fetch_tenant_sources ───────────────────────────────────────────────

class TenantSource(BaseModel):
    id: uuid.UUID
    url: str
    type: str                           # "rss" | "html"
    last_scraped_at: datetime | None = None
    last_etag: str | None = None
    last_content_hash: str | None = None
    # Nested scraping config
    max_depth: int = 1                  # how many link levels to follow
    same_domain_only: bool = True       # restrict link following to root domain
    include_patterns: list[str] = Field(default_factory=list)   # URL substrings filter
    max_child_links_per_page: int = 4   # max child URLs to enqueue per fetched page
    max_links_to_scrape: int = 25       # max distinct URLs per source (total cap)
    exclude_patterns: list[str] = Field(default_factory=list)   # URL substrings to skip
    min_text_chars: int = 40            # min body length to create an article
    require_title: bool = True          # skip insert if title empty


class FetchTenantSourcesOutput(BaseModel):
    sources: list[TenantSource]
    tag_taxonomy: list[str]
    format_template: dict[str, Any]
    format_template_id: uuid.UUID | None


# ── Skill: scrape_source_urls_incremental ─────────────────────────────────────

class RawItem(BaseModel):
    source_id: uuid.UUID
    url: str
    raw_html: str
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str
    depth: int = 0                      # depth at which this URL was discovered


# ── Skill: extract_and_normalize_articles ─────────────────────────────────────

class NormalizedArticle(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: uuid.UUID
    url: str
    canonical_url: str | None = None
    title: str
    author: str | None = None
    published_at: datetime | None = None
    img_url: str | None = None          # featured / hero image URL
    summary: str | None = None          # short summary (extracted or auto-generated)
    text: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Skill: tag_content_item ───────────────────────────────────────────────────

class TaggingOutput(BaseModel):
    article_id: uuid.UUID
    tags: list[str]                     # max 6
    tag_confidences: dict[str, float]


# ── Ingestion run summary (returned as AgentInvocationResult.output) ──────────

class IngestionSummary(BaseModel):
    tenant_id: str
    execution_id: str
    sources_scraped: int = 0
    sources_skipped: int = 0
    urls_visited: int = 0               # total URLs visited across all depths
    new_articles: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    estimated_cost_usd: float = 0.0
