"""
Pydantic models for the content curation skill.

These define the typed boundaries for each step of the curation pipeline:
  scrape → extract → match products → generate newsletter → save
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TenantSource(BaseModel):
    id: uuid.UUID
    url: str
    type: str
    max_depth: int = 1
    same_domain_only: bool = True
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    max_child_links_per_page: int = 4
    max_links_to_scrape: int = 25
    min_text_chars: int = 40
    require_title: bool = True


class TenantProduct(BaseModel):
    id: uuid.UUID
    name: str
    description: str = ""
    url: str | None = None
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)


class ScrapedContent(BaseModel):
    source_id: uuid.UUID
    url: str
    title: str = ""
    text: str = ""
    content_hash: str = ""
    depth: int = 0


class ExtractedArticle(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: uuid.UUID
    url: str
    title: str
    summary: str = ""
    text: str = ""
    tags: list[str] = Field(default_factory=list)


class ProductMatch(BaseModel):
    product_id: uuid.UUID
    product_name: str
    relevance_score: float = 0.0
    match_reason: str = ""


class NewsletterArticle(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    article_id: uuid.UUID | None = None
    title: str
    summary: str = ""
    body: str = ""
    product_refs: list[ProductMatch] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_url: str = ""
    status: str = "draft"


class CurationSummary(BaseModel):
    execution_id: str
    tenant_id: str
    sources_scraped: int = 0
    pages_fetched: int = 0
    articles_extracted: int = 0
    newsletter_articles_created: int = 0
    products_matched: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    estimated_cost_usd: float = 0.0
