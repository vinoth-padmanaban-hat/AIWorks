"""
Policy domain models.

ScrapingLimits  — per-tenant crawl quotas enforced by the Scraper MCP server.
EffectivePolicy — resolved policy snapshot passed to agents and tools.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScrapingLimits(BaseModel):
    """
    Per-tenant crawl quotas.  Stored in tenant_policies.scraping_limits_json.
    Enforced by the Scraper MCP server before any crawl begins.
    """

    max_depth: int = Field(2, ge=0, le=10, description="Max crawl depth from seed URL.")
    max_links_per_page: int = Field(
        30, ge=1, le=500, description="Max links to follow from a single page."
    )
    max_total_links: int = Field(
        100, ge=1, le=10_000, description="Hard cap on total URLs scraped per execution."
    )
    allow_external_domains: bool = Field(
        False, description="Whether the crawler may follow links to other domains."
    )
    allow_subdomains: bool = Field(
        True, description="Whether subdomains of the seed domain are permitted."
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Explicit domain allowlist (overrides allow_external_domains).",
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        description="Domains that must never be visited.",
    )
    max_concurrent_requests: int = Field(
        3, ge=1, le=20, description="Parallelism cap for the crawler."
    )
    request_delay_ms: int = Field(
        500, ge=0, description="Minimum delay (ms) between requests to the same host."
    )
    default_visit_strategy: str = Field(
        "skip_if_seen",
        description=(
            "Fallback visit strategy applied to all sources that have no explicit "
            "visit_strategy set. One of: skip_if_seen, revisit_if_changed, "
            "always_revisit, revisit_after_ttl."
        ),
    )
    revisit_ttl_hours: int = Field(
        24,
        ge=1,
        description=(
            "Default TTL (hours) for the revisit_after_ttl strategy. "
            "A source-level revisit_ttl_hours overrides this."
        ),
    )

    @classmethod
    def default(cls) -> "ScrapingLimits":
        return cls()

    def to_scraping_config(self) -> dict[str, Any]:
        """Serialise to the dict shape expected by Scraper MCP tool requests."""
        return self.model_dump()


class EffectivePolicy(BaseModel):
    """
    Resolved policy snapshot for one (tenant, persona) pair.
    Passed to the planner, execution engine, and agents.
    """

    raw: dict[str, Any] = Field(default_factory=dict)
    scraping_limits: ScrapingLimits = Field(default_factory=ScrapingLimits)

    # ── convenience accessors ──────────────────────────────────────────────────

    @property
    def allowed_skills(self) -> list[str]:
        return self.raw.get("capabilities", {}).get("allowed", [])

    @property
    def blocked_skills(self) -> list[str]:
        return self.raw.get("capabilities", {}).get("blocked", [])

    @property
    def default_allow(self) -> bool:
        return self.raw.get("capabilities", {}).get("defaultAllow", True)

    @property
    def per_execution_usd_limit(self) -> float:
        return float(self.raw.get("budget", {}).get("perExecutionUsdLimit", 1.0))

    @property
    def max_tokens(self) -> int:
        return int(self.raw.get("budget", {}).get("maxTokensPerExecution", 50_000))

    @property
    def allow_web_scraping(self) -> bool:
        return self.raw.get("security", {}).get("allowWebScraping", True)
