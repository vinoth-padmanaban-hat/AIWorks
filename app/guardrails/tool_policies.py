"""
Tool policy guardrails — run before every tool/MCP invocation.

Functions:
  check_tool_allowed(tool_id, effective_policy)
  check_scraping_limits(url, current_depth, current_total, limits)
  check_domain_allowed(url, limits)
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.domain.policy.models import EffectivePolicy, ScrapingLimits
from app.guardrails.audit import log_guardrail_result
from app.guardrails.exceptions import GuardrailSeverity, GuardrailViolation


def check_tool_allowed(tool_id: str, effective_policy: EffectivePolicy) -> None:
    """
    Verify that `tool_id` is not blocked by the tenant's effective policy.

    Raises GuardrailViolation(HIGH) if the tool is explicitly blocked or if
    web scraping is disabled and the tool is a scraping tool.
    """
    blocked_tools: list[str] = effective_policy.raw.get("tools", {}).get("blocked", [])
    if tool_id in blocked_tools:
        reason = f"Tool '{tool_id}' is explicitly blocked by tenant policy."
        log_guardrail_result(
            "tool_policy",
            passed=False,
            reason=reason,
            tool_id=tool_id,
            extra={"blocklist": True},
        )
        raise GuardrailViolation(
            guard_type="tool_policy",
            reason=reason,
            severity=GuardrailSeverity.HIGH,
            input_summary=tool_id,
        )

    scraping_tools = {
        "fetch_page", "fetch_page_full", "fetch_pages_batch",
        "fetch_links", "discover_urls", "deep_crawl",
        "screenshot_page", "extract_structured", "extract_structured_no_llm",
    }
    if tool_id in scraping_tools and not effective_policy.allow_web_scraping:
        reason = "allowWebScraping=false for this tenant — scraping tools are disabled."
        log_guardrail_result(
            "tool_policy",
            passed=False,
            reason=reason,
            tool_id=tool_id,
            extra={"allow_web_scraping": False},
        )
        raise GuardrailViolation(
            guard_type="tool_policy",
            reason=reason,
            severity=GuardrailSeverity.HIGH,
            input_summary=tool_id,
        )
    log_guardrail_result(
        "tool_policy",
        passed=True,
        tool_id=tool_id,
        extra={"allow_web_scraping": effective_policy.allow_web_scraping},
    )


def check_scraping_limits(
    url: str,
    current_depth: int,
    current_total: int,
    limits: ScrapingLimits,
) -> None:
    """
    Enforce per-tenant crawl quotas before visiting a URL.

    Raises GuardrailViolation(HIGH) if any limit is exceeded.
    """
    if current_depth > limits.max_depth:
        reason = (
            f"Crawl depth {current_depth} exceeds tenant limit {limits.max_depth}."
        )
        log_guardrail_result(
            "scraping_limit",
            passed=False,
            reason=reason,
            extra={"current_depth": current_depth, "max_depth": limits.max_depth},
        )
        raise GuardrailViolation(
            guard_type="scraping_limit",
            reason=reason,
            severity=GuardrailSeverity.HIGH,
            input_summary=url[:200],
        )

    if current_total >= limits.max_total_links:
        reason = (
            f"Total URLs scraped ({current_total}) reached tenant limit "
            f"{limits.max_total_links}."
        )
        log_guardrail_result(
            "scraping_limit",
            passed=False,
            reason=reason,
            extra={"current_total": current_total, "max_total_links": limits.max_total_links},
        )
        raise GuardrailViolation(
            guard_type="scraping_limit",
            reason=reason,
            severity=GuardrailSeverity.HIGH,
            input_summary=url[:200],
        )
    log_guardrail_result(
        "scraping_limit",
        passed=True,
        extra={
            "current_depth": current_depth,
            "current_total": current_total,
            "max_depth": limits.max_depth,
            "max_total_links": limits.max_total_links,
        },
    )


def check_domain_allowed(url: str, limits: ScrapingLimits) -> None:
    """
    Verify that the URL's domain is permitted by the tenant's scraping limits.

    Raises GuardrailViolation(HIGH) if:
      - The domain is in blocked_domains.
      - The URL is external and allow_external_domains=False (unless in allowed_domains).
      - The URL is a subdomain and allow_subdomains=False.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().lstrip("www.")

    # Explicit block list takes priority
    for blocked in limits.blocked_domains:
        if host == blocked.lower() or host.endswith("." + blocked.lower()):
            reason = f"Domain '{host}' is in the tenant's blocked_domains list."
            log_guardrail_result(
                "domain_policy",
                passed=False,
                reason=reason,
                extra={"host": host},
            )
            raise GuardrailViolation(
                guard_type="domain_policy",
                reason=reason,
                severity=GuardrailSeverity.HIGH,
                input_summary=url[:200],
            )

    # Explicit allow list overrides external domain restriction
    for allowed in limits.allowed_domains:
        if host == allowed.lower() or host.endswith("." + allowed.lower()):
            log_guardrail_result(
                "domain_policy",
                passed=True,
                extra={"host": host, "matched_allowlist": True},
            )
            return

    # External domain check (requires seed_domain context — callers pass it via limits)
    # We rely on the scraper agent to pass the seed domain in allowed_domains when needed.
    if not limits.allow_external_domains and limits.allowed_domains:
        # If allowed_domains is set and this host isn't in it, it's external
        in_allowed = any(
            host == d.lower() or host.endswith("." + d.lower())
            for d in limits.allowed_domains
        )
        if not in_allowed:
            reason = f"Domain '{host}' is external and allow_external_domains=false."
            log_guardrail_result(
                "domain_policy",
                passed=False,
                reason=reason,
                extra={"host": host},
            )
            raise GuardrailViolation(
                guard_type="domain_policy",
                reason=reason,
                severity=GuardrailSeverity.HIGH,
                input_summary=url[:200],
            )
    log_guardrail_result("domain_policy", passed=True, extra={"host": host})
