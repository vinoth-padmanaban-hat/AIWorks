"""
Policy Engine — enforces tenant/persona rules at pre-planning and pre-execution.

Checks:
  1. Pre-planning : is the requested skill allowed for this tenant?
  2. Pre-execution: does the agent/tool choice comply with effective policy?
  3. Budget guard : would this execution exceed perExecutionUsdLimit?
  4. Approval flag: does a capability require human approval? (logged for PoC)
  5. Scraping limits: per-tenant crawl quotas (max_depth, max_total_links, …)

PolicyEngine never modifies state — it only reads policy and returns verdicts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy.models import EffectivePolicy, ScrapingLimits

logger = logging.getLogger(__name__)

# ── Default policy (permissive defaults for development / new tenants) ─────────
_DEFAULT_POLICY: dict[str, Any] = {
    "capabilities": {
        "allowed": [
            "fetch_tenant_sources",
            "scrape_source_urls_incremental",
            "extract_and_normalize_articles",
            "tag_content_item",
            "apply_article_format_template",
            "record_ingestion_log_entry",
            "content_curation",
            "match_products",
            "generate_newsletter",
            "scrape_urls",
            "search_and_scrape",
            "match_content_to_entities",
        ],
        "blocked": [],
        "requireApproval": [],
        "defaultAllow": True,
    },
    "budget": {
        "perExecutionUsdLimit": 1.0,
        "maxTokensPerExecution": 50_000,
    },
    "security": {
        "allowWebScraping": True,
        "allowExternalApiCalls": True,
    },
}

_DEFAULT_SCRAPING_LIMITS: dict[str, Any] = {
    "max_depth": 2,
    "max_links_per_page": 30,
    "max_total_links": 100,
    "allow_external_domains": False,
    "allow_subdomains": True,
    "allowed_domains": [],
    "blocked_domains": [],
    "max_concurrent_requests": 3,
    "request_delay_ms": 500,
    "default_visit_strategy": "skip_if_seen",
    "revisit_ttl_hours": 24,
}


@dataclass
class PolicyViolation:
    capability: str
    reason: str


@dataclass
class PolicyCheckResult:
    allowed: bool
    requires_approval: bool = False
    violations: list[PolicyViolation] = field(default_factory=list)
    effective_policy: dict[str, Any] = field(default_factory=dict)
    scraping_limits: ScrapingLimits = field(default_factory=ScrapingLimits)

    @property
    def blocked_capabilities(self) -> list[str]:
        return self.effective_policy.get("capabilities", {}).get("blocked", [])

    @property
    def approval_required_capabilities(self) -> list[str]:
        return self.effective_policy.get("capabilities", {}).get("requireApproval", [])

    @property
    def per_execution_usd_limit(self) -> float:
        return float(self.effective_policy.get("budget", {}).get("perExecutionUsdLimit", 1.0))

    @property
    def max_tokens(self) -> int:
        return int(self.effective_policy.get("budget", {}).get("maxTokensPerExecution", 50_000))

    def to_effective_policy(self) -> EffectivePolicy:
        return EffectivePolicy(raw=self.effective_policy, scraping_limits=self.scraping_limits)


class PolicyEngine:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_effective_policy(self, tenant_id: uuid.UUID) -> EffectivePolicy:
        """
        Load tenant policy + scraping limits from DB.
        Falls back to defaults if the tenant has no explicit policy row.
        """
        result = await self._db.execute(
            text(
                "SELECT policy_json, scraping_limits_json "
                "FROM tenant_policies WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )
        row = result.fetchone()

        if row:
            raw_policy = dict(row.policy_json)
            raw_limits = dict(row.scraping_limits_json) if row.scraping_limits_json else {}
        else:
            logger.warning("No policy found for tenant %s — using defaults", tenant_id)
            raw_policy = _DEFAULT_POLICY
            raw_limits = {}

        scraping_limits = ScrapingLimits(**{**_DEFAULT_SCRAPING_LIMITS, **raw_limits})

        logger.debug(
            "[Policy] effective_policy tenant=%s scraping_limits=%s",
            tenant_id,
            scraping_limits.model_dump(),
        )

        return EffectivePolicy(raw=raw_policy, scraping_limits=scraping_limits)

    async def check_skill(
        self,
        skill_id: str,
        tenant_id: uuid.UUID,
        effective_policy: EffectivePolicy | None = None,
    ) -> PolicyCheckResult:
        """
        Check whether skill_id is permitted for tenant_id.

        Returns a PolicyCheckResult with:
          - allowed: can the execution proceed?
          - requires_approval: human must approve before side effects
          - violations: list of specific rule violations
          - effective_policy: the resolved policy snapshot
          - scraping_limits: the tenant's crawl quotas
        """
        ep = effective_policy or await self.get_effective_policy(tenant_id)
        policy = ep.raw
        caps = policy.get("capabilities", {})
        blocked: list[str] = caps.get("blocked", [])
        allowed_list: list[str] = caps.get("allowed", [])
        default_allow: bool = caps.get("defaultAllow", True)
        require_approval: list[str] = caps.get("requireApproval", [])
        security = policy.get("security", {})

        violations: list[PolicyViolation] = []

        if skill_id in blocked:
            violations.append(
                PolicyViolation(
                    capability=skill_id,
                    reason=f"Capability '{skill_id}' is explicitly blocked for this tenant.",
                )
            )

        if not default_allow and allowed_list and skill_id not in allowed_list:
            violations.append(
                PolicyViolation(
                    capability=skill_id,
                    reason=(
                        f"Capability '{skill_id}' is not in the allowed list "
                        "and defaultAllow=false."
                    ),
                )
            )

        if "scrape" in skill_id and not security.get("allowWebScraping", True):
            violations.append(
                PolicyViolation(
                    capability=skill_id,
                    reason="allowWebScraping=false for this tenant.",
                )
            )

        requires_approval = skill_id in require_approval
        if requires_approval:
            logger.info(
                "[Policy] skill '%s' requires human approval for tenant %s",
                skill_id,
                tenant_id,
            )

        allowed = len(violations) == 0
        if not allowed:
            logger.warning(
                "[Policy] BLOCKED skill='%s' tenant=%s violations=%s",
                skill_id,
                tenant_id,
                [v.reason for v in violations],
            )
        else:
            logger.debug("[Policy] ALLOWED skill='%s' tenant=%s", skill_id, tenant_id)

        return PolicyCheckResult(
            allowed=allowed,
            requires_approval=requires_approval,
            violations=violations,
            effective_policy=policy,
            scraping_limits=ep.scraping_limits,
        )
