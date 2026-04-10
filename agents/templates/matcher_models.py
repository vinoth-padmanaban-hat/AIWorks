"""Pydantic I/O models for the Generic Content Matcher Agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MatcherAgentInput(BaseModel):
    """Input to the generic content matcher agent."""

    # Content to match against
    content: str = Field(..., description="Text content to match (article, query, document).")
    content_url: str = Field("", description="Source URL of the content (for deduplication).")

    # What to match against
    entity_type: str = Field(
        ...,
        description=(
            "Type of entity to match against. "
            "Examples: 'product', 'kb_article', 'legal_case', 'hr_policy'."
        ),
    )
    entity_table: str = Field(
        ...,
        description=(
            "Tenant DB table to search. "
            "Examples: 'tenant_products', 'kb_articles', 'litigation_cases'."
        ),
    )
    match_fields: list[str] = Field(
        default_factory=lambda: ["name", "description"],
        description="DB columns to include in match candidates.",
    )

    # Match config
    top_k: int = Field(5, ge=1, le=50, description="Max number of matches to return.")
    min_score: float = Field(0.0, ge=0.0, le=1.0, description="Minimum relevance score (0–1).")
    use_vector_search: bool = Field(True, description="Use vector similarity search.")
    use_db_search: bool = Field(True, description="Use keyword/metadata DB search.")
    use_llm_rerank: bool = Field(True, description="LLM re-ranks candidates before returning.")

    # Tracing context
    execution_id: str = ""
    tenant_id: str = ""
    step_id: str = ""


class MatchResult(BaseModel):
    """One matched entity."""

    entity_id: str
    entity_type: str
    name: str
    description: str = ""
    score: float = 0.0
    match_reason: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class MatcherAgentOutput(BaseModel):
    """Output from the generic content matcher agent."""

    matches: list[MatchResult] = Field(default_factory=list)
    total_candidates: int = 0
    total_matches: int = 0
    execution_id: str = ""
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)
