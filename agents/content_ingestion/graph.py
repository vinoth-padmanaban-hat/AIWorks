"""
LangGraph ingestion graph for the content_ingestion_agent service.

DB Architecture:
  - CONTROL PLANE DB  : policy, registries (accessed via app.core.db.AsyncSessionLocal)
  - TENANT DB         : all domain data — sources, articles, tags, logs
                        (accessed via app.core.tenant_db.get_tenant_db_session)
  - Tenant DB tables have NO tenant_id columns; the DB boundary is the tenant boundary.

Nodes:
  1. load_tenant_config          → fetch_tenant_sources
  2. scrape_sources_incremental  → scrape_source_urls_incremental
                                    (BFS nested scraping up to max_depth per source)
  3. normalize_articles          → extract_and_normalize_articles
  4. tag_and_format_articles     → tag_content_item + apply_article_format_template
  5. summarize_execution         → record_ingestion_log_entry + ingestion_executions

Policy enforcement:
  - "tag_content_item" blocked      → skip tagging, store empty tags.
  - "apply_article_format_template" needs approval → log notice, proceed for PoC.
  - Budget cap: stop tag loop early if accumulated cost > perExecutionUsdLimit.

Nested scraping BFS:
  - For each source: start at root URL, follow discovered links up to max_depth.
  - Respects same_domain_only and include_patterns from tenant_sources.
  - visited_urls set prevents revisiting URLs within the same execution.
  - content_hash comparison skips unchanged pages.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from sqlalchemy import text

from app.core.config import settings
from app.core.langfuse_setup import graph_runnable_config, langfuse_trace
from app.core.tenant_db import get_tenant_db_session
from app.domain.policy.models import EffectivePolicy, ScrapingLimits
from agents.content_ingestion.models import NormalizedArticle
from tools.scraper_mcp.client import ScraperMCPClient
from tools.scraper_mcp.helpers import (
    compact_media_payload,
    html_to_text,
    pick_primary_image_url,
)

logger = logging.getLogger(__name__)

_COST_IN  = 0.15 / 1_000_000   # $ per input token  (gpt-4o-mini)
_COST_OUT = 0.60 / 1_000_000   # $ per output token

llm = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0,
)

scraper = ScraperMCPClient(
    base_url=settings.scraper_mcp_url,
    timeout_seconds=settings.scraper_http_timeout_seconds,
)

_SEP = "─" * 72


# ── Graph state ────────────────────────────────────────────────────────────────

class IngestionState(TypedDict, total=False):
    tenant_id: str
    execution_id: str
    effective_policy: dict          # passed from Orchestrator via AgentInvocationContext
    persona_id: str | None          # control plane Persona Store id
    persona: dict | None            # PersonaSnapshot as JSON
    persona_summary: str | None     # compact string for prompts / logs
    # Node 1
    sources: list[dict]
    tag_taxonomy: list[str]
    format_template: dict
    format_template_id: str | None
    # Node 2
    raw_items: list[dict]
    sources_scraped: int
    sources_skipped: int
    urls_visited: int
    # Node 3
    articles: list[dict]
    new_articles: int
    # Node 4 (accumulated)
    total_tokens_in: int
    total_tokens_out: int
    estimated_cost_usd: float
    # Node 5
    summary: dict
    error: str | None


# ── Policy helpers ─────────────────────────────────────────────────────────────

def _is_capability_blocked(capability: str, policy: dict) -> bool:
    return capability in policy.get("capabilities", {}).get("blocked", [])


def _requires_approval(capability: str, policy: dict) -> bool:
    return capability in policy.get("capabilities", {}).get("requireApproval", [])


def _budget_limit(policy: dict) -> float:
    return float(policy.get("budget", {}).get("perExecutionUsdLimit", 1.0))


def _url_matches_exclude(url: str, exclude_patterns: list[str]) -> bool:
    """True if URL should be skipped (matches any exclude substring)."""
    if not exclude_patterns:
        return False
    return any(p and p in url for p in exclude_patterns)


_VALID_VISIT_STRATEGIES = frozenset(
    {"skip_if_seen", "revisit_if_changed", "always_revisit", "revisit_after_ttl"}
)


def _resolve_visit_strategy(
    src: dict,
    policy: EffectivePolicy,
) -> tuple[str, int]:
    """
    Return (visit_strategy, revisit_ttl_hours) for a source.

    Priority: source-level setting > policy default > hardcoded fallback.
    """
    strategy = src.get("visit_strategy") or policy.scraping_limits.default_visit_strategy
    if strategy not in _VALID_VISIT_STRATEGIES:
        logger.warning(
            "Unknown visit_strategy=%r for source %s — falling back to skip_if_seen",
            strategy, src.get("url"),
        )
        strategy = "skip_if_seen"
    ttl = int(src.get("revisit_ttl_hours") or policy.scraping_limits.revisit_ttl_hours or 24)
    return strategy, ttl


def _should_visit(
    url: str,
    strategy: str,
    in_memory_visited: set[str],
    db_known_urls: set[str],
    last_scraped_at: datetime | None,
    revisit_ttl_hours: int,
    *,
    is_seed_url: bool = False,
) -> tuple[bool, str]:
    """
    Decide whether to fetch a URL based on the configured visit strategy.

    Returns (should_visit: bool, reason: str).

    The in-memory visited set is always checked first — it prevents revisiting
    a URL more than once within a single execution regardless of strategy.

    Configured source roots (`is_seed_url`) are always fetched when a run executes
    so crawls can discover new links and unchanged seeds still enqueue children.
    Article deduplication is enforced at insert time (`articles.url` unique),
    not by skipping the seed URL.

    Strategy semantics (non-seed URLs):
      skip_if_seen      — skip if URL is already stored in the articles table.
      revisit_if_changed — always fetch; the scraper's content-hash comparison
                           decides whether to process (result.changed).
      always_revisit    — always fetch and re-process unconditionally.
      revisit_after_ttl — skip unless last_scraped_at is older than ttl hours.
    """
    # Within-run guard — always applied, regardless of strategy
    if url in in_memory_visited:
        return False, "in_memory_visited"

    if is_seed_url:
        return True, f"visit:{strategy}:seed_url"

    if strategy == "skip_if_seen":
        if url in db_known_urls:
            return False, "skip_if_seen:in_db"

    elif strategy == "revisit_after_ttl":
        if last_scraped_at is not None:
            age_hours = (datetime.now(timezone.utc) - last_scraped_at).total_seconds() / 3600
            if age_hours < revisit_ttl_hours:
                return False, f"revisit_after_ttl:age={age_hours:.1f}h<{revisit_ttl_hours}h"

    # always_revisit and revisit_if_changed: always proceed to fetch
    return True, f"visit:{strategy}"


# ── DB helpers (tenant DB — no tenant_id columns) ─────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _log_entry(
    db: Any,
    *,
    execution_id: str,
    step_name: str,
    status: str,
    details: dict,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    source_id: str | None = None,
    article_id: str | None = None,
) -> None:
    """Insert an ingestion_log_entry row into the TENANT DB (no tenant_id column)."""
    await db.execute(
        text(
            """
            INSERT INTO ingestion_log_entries
                (execution_id, source_id, article_id,
                 step_name, status, details_json,
                 tokens_in, tokens_out, cost_usd, duration_ms)
            VALUES
                (:eid, :sid, :aid,
                 :step, :status, CAST(:details AS jsonb),
                 :ti, :to, :cost, :dur)
            """
        ),
        {
            "eid":    uuid.UUID(execution_id),
            "sid":    uuid.UUID(source_id)  if source_id  else None,
            "aid":    uuid.UUID(article_id) if article_id else None,
            "step":   step_name,
            "status": status,
            "details": json.dumps(details),
            "ti":     tokens_in,
            "to":     tokens_out,
            "cost":   cost_usd,
            "dur":    duration_ms,
        },
    )


# ── Node 1: load_tenant_config ─────────────────────────────────────────────────

async def load_tenant_config(state: IngestionState) -> dict:
    tid_str = state["tenant_id"]
    eid     = state["execution_id"]
    tid     = uuid.UUID(tid_str)
    t0      = time.monotonic()

    logger.info(_SEP)
    logger.info(
        "NODE 1 / load_tenant_config  |  execution_id=%-36s  tenant=%s",
        eid, tid_str,
    )
    if state.get("persona_summary"):
        logger.info(
            "  persona_id=%s  summary=%s",
            state.get("persona_id"),
            (state.get("persona_summary") or "")[:160],
        )

    async with get_tenant_db_session(tid) as db:
        # ── Sources ──────────────────────────────────────────────────────────
        src_rows = await db.execute(
            text(
                "SELECT id, url, type, last_scraped_at, last_etag, last_content_hash,"
                "       max_depth, same_domain_only, include_patterns,"
                "       max_child_links_per_page, max_links_to_scrape,"
                "       exclude_patterns, min_text_chars, require_title,"
                "       visit_strategy, revisit_ttl_hours "
                "FROM tenant_sources WHERE active = true ORDER BY url"
            )
        )
        sources = [
            {
                "id":                        str(r.id),
                "url":                       r.url,
                "type":                      r.type,
                "last_scraped_at":           r.last_scraped_at.isoformat() if r.last_scraped_at else None,
                "last_etag":                 r.last_etag,
                "last_content_hash":         r.last_content_hash,
                "max_depth":                 r.max_depth,
                "same_domain_only":          r.same_domain_only,
                "include_patterns":          list(r.include_patterns or []),
                "max_child_links_per_page":  r.max_child_links_per_page,
                "max_links_to_scrape":       r.max_links_to_scrape,
                "exclude_patterns":          list(r.exclude_patterns or []),
                "min_text_chars":            r.min_text_chars,
                "require_title":             r.require_title,
                "visit_strategy":            r.visit_strategy,
                "revisit_ttl_hours":         r.revisit_ttl_hours,
            }
            for r in src_rows.fetchall()
        ]

        # ── Tag taxonomy ─────────────────────────────────────────────────────
        tag_rows = await db.execute(
            text("SELECT name FROM tenant_tags ORDER BY name")
        )
        taxonomy = [r.name for r in tag_rows.fetchall()]

        # ── Default format template ───────────────────────────────────────────
        tmpl_row = await db.execute(
            text(
                "SELECT id, template_json FROM tenant_article_format_templates "
                "WHERE is_default = true LIMIT 1"
            )
        )
        tmpl = tmpl_row.fetchone()

        # ── Log ───────────────────────────────────────────────────────────────
        await _log_entry(
            db,
            execution_id=eid,
            step_name="load_tenant_config",
            status="SUCCESS",
            details={"sources_found": len(sources), "tags_found": len(taxonomy)},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        await db.commit()

    logger.info(
        "  sources=%d  tags=%d  template=%s  duration_ms=%d",
        len(sources), len(taxonomy),
        tmpl.id if tmpl else "NONE",
        int((time.monotonic() - t0) * 1000),
    )
    for src in sources:
        logger.info(
            "    source  url=%-50s  depth=%d  child_cap=%d  total_cap=%d  same_domain=%s",
            src["url"][:50],
            src["max_depth"],
            src.get("max_child_links_per_page", 4),
            src.get("max_links_to_scrape", 25),
            src["same_domain_only"],
        )
        logger.info(
            "            visit_strategy=%s  revisit_ttl_hours=%s  "
            "include=%s  exclude=%s  min_chars=%s  require_title=%s",
            src.get("visit_strategy", "skip_if_seen"),
            src.get("revisit_ttl_hours", 24),
            src.get("include_patterns") or [],
            src.get("exclude_patterns") or [],
            src.get("min_text_chars", 40),
            src.get("require_title", True),
        )

    return {
        "sources":            sources,
        "tag_taxonomy":       taxonomy,
        "format_template":    tmpl.template_json if tmpl else {},
        "format_template_id": str(tmpl.id) if tmpl else None,
    }


# ── Node 2: scrape_sources_incremental (BFS nested scraping) ──────────────────

async def scrape_sources_incremental(state: IngestionState) -> dict:
    eid    = state["execution_id"]
    tid    = uuid.UUID(state["tenant_id"])
    raw_items: list[dict] = []
    scraped = skipped = total_urls = 0

    # Reconstruct EffectivePolicy from the dict passed via AgentInvocationContext
    raw_policy: dict = state.get("effective_policy") or {}
    effective_policy = EffectivePolicy(
        raw=raw_policy,
        scraping_limits=ScrapingLimits(
            **{
                **{
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
                },
                **raw_policy.get("scraping_limits", {}),
            }
        ),
    )

    logger.info(_SEP)
    logger.info(
        "NODE 2 / scrape_sources_incremental  |  sources=%d  execution_id=%s",
        len(state["sources"]), eid,
    )

    async with get_tenant_db_session(tid) as db:
        for src in state["sources"]:
            root_url         = src["url"]
            configured_depth = int(src.get("max_depth", 1) or 1)
            max_depth        = min(configured_depth, settings.ingestion_max_depth_cap)
            same_domain_only = src.get("same_domain_only", True)
            include_patterns = src.get("include_patterns", [])
            exclude_patterns = list(src.get("exclude_patterns") or [])
            max_child_links  = int(src.get("max_child_links_per_page", 4) or 4)
            configured_total = int(src.get("max_links_to_scrape", 25) or 25)
            max_links_per_source = min(configured_total, settings.ingestion_max_links_per_source)

            # ── Resolve visit strategy for this source ────────────────────────
            visit_strategy, revisit_ttl_hours = _resolve_visit_strategy(src, effective_policy)

            logger.info(
                "  ┌─ source  url=%-55s  max_depth=%d  same_domain=%s",
                root_url, max_depth, same_domain_only,
            )
            logger.info(
                "  │  limits depth_cfg=%d  depth_cap=%d  child_links_per_url=%d  total_url_cap=%d",
                configured_depth,
                settings.ingestion_max_depth_cap,
                max_child_links,
                max_links_per_source,
            )
            logger.info(
                "  │  visit_strategy=%s  revisit_ttl_hours=%d",
                visit_strategy, revisit_ttl_hours,
            )

            # ── Pre-seed visited from DB to avoid redundant network calls ─────
            # For skip_if_seen: load all URLs already stored for this source so
            # the BFS never fetches a page we already have, saving network + tokens.
            # For revisit_after_ttl: load URL → last_scraped_at mapping for TTL checks.
            # Other strategies: start with only the root URL in visited.
            db_known_urls: set[str] = set()
            url_last_scraped: dict[str, datetime] = {}

            if visit_strategy == "skip_if_seen":
                known_rows = await db.execute(
                    text("SELECT url FROM articles WHERE source_id = :sid"),
                    {"sid": uuid.UUID(src["id"])},
                )
                db_known_urls = {r.url for r in known_rows.fetchall()}
                logger.info(
                    "  │  pre-seeded visited with %d known URLs from DB (skip_if_seen)",
                    len(db_known_urls),
                )

            elif visit_strategy == "revisit_after_ttl":
                ttl_rows = await db.execute(
                    text(
                        "SELECT url, created_at FROM articles "
                        "WHERE source_id = :sid ORDER BY created_at DESC"
                    ),
                    {"sid": uuid.UUID(src["id"])},
                )
                for r in ttl_rows.fetchall():
                    if r.url not in url_last_scraped:
                        url_last_scraped[r.url] = r.created_at
                logger.info(
                    "  │  loaded last_scraped_at for %d URLs (revisit_after_ttl  ttl=%dh)",
                    len(url_last_scraped), revisit_ttl_hours,
                )

            # BFS queue: (url, depth)
            # visited is the in-memory within-run guard — always populated regardless of strategy
            queue: deque[tuple[str, int]] = deque([(root_url, 0)])
            visited: set[str] = {root_url}
            source_new = source_skipped = 0

            while queue:
                if len(visited) > max_links_per_source:
                    logger.warning(
                        "  │  MAX LINKS LIMIT HIT  source=%s  visited=%d  limit=%d  "
                        "dropping remaining queue=%d",
                        root_url,
                        len(visited),
                        max_links_per_source,
                        len(queue),
                    )
                    await _log_entry(
                        db,
                        execution_id=eid,
                        step_name="scrape_source_limit",
                        status="NO_CHANGE",
                        details={
                            "url": root_url,
                            "reason": "max_links_per_source_reached",
                            "visited": len(visited),
                            "limit": max_links_per_source,
                        },
                        source_id=src["id"],
                    )
                    break

                url, depth = queue.popleft()
                total_urls += 1
                t0 = time.monotonic()

                # ── Visit strategy gate ───────────────────────────────────────
                # in_memory_visited is not re-checked here because the visited set
                # is already enforced at enqueue time (child links are only queued
                # when not in visited). This gate handles cross-run decisions only.
                url_scraped_at = url_last_scraped.get(url)
                should_visit, visit_reason = _should_visit(
                    url=url,
                    strategy=visit_strategy,
                    in_memory_visited=set(),   # in-memory guard enforced at enqueue; skip here
                    db_known_urls=db_known_urls,
                    last_scraped_at=url_scraped_at,
                    revisit_ttl_hours=revisit_ttl_hours,
                    is_seed_url=(url == root_url),
                )
                if not should_visit:
                    source_skipped += 1
                    skipped += 1
                    logger.info(
                        "  │  SKIP  url=%-55s  reason=%s",
                        url, visit_reason,
                    )
                    await _log_entry(
                        db,
                        execution_id=eid,
                        step_name="scrape_url",
                        status="NO_CHANGE",
                        details={"url": url, "depth": depth, "reason": visit_reason},
                        source_id=src["id"],
                    )
                    continue

                # ── Fetch page content ────────────────────────────────────────
                # Pass last_content_hash for revisit_if_changed so the scraper can
                # short-circuit with changed=False when the page hasn't changed.
                # For always_revisit we pass None to force re-processing.
                if visit_strategy == "revisit_if_changed":
                    last_hash = src.get("last_content_hash") if url == root_url else None
                elif visit_strategy in ("skip_if_seen", "revisit_after_ttl"):
                    last_hash = src.get("last_content_hash") if url == root_url else None
                else:
                    last_hash = None  # always_revisit: ignore cached hash

                logger.info(
                    "  │  VISIT  depth=%d  strategy=%s  url=%s",
                    depth, visit_strategy, url,
                )

                result = await scraper.fetch_page_full(
                    url=url,
                    last_content_hash=last_hash,
                    include_media=True,
                    include_links=False,
                    include_raw_html=True,
                )
                dur = result.duration_ms

                if result.error:
                    source_skipped += 1
                    skipped += 1
                    logger.error(
                        "  │  FETCH_ERROR  url=%-55s  error=%s",
                        url, result.error,
                    )
                    await _log_entry(
                        db,
                        execution_id=eid,
                        step_name="scrape_url",
                        status="ERROR",
                        details={
                            "url": url,
                            "depth": depth,
                            "reason": "scraper_http_error",
                            "error": result.error,
                        },
                        duration_ms=dur,
                        source_id=src["id"],
                    )
                    continue

                # For revisit_if_changed: skip processing when content unchanged,
                # but still follow child links (the page structure may have new links).
                content_unchanged = (
                    visit_strategy == "revisit_if_changed"
                    and (not result.changed or result.status_code in (0, 304))
                )
                if content_unchanged:
                    source_skipped += 1
                    skipped += 1
                    logger.info(
                        "  │  NO_CHANGE  url=%-55s  status=%d  hash=%s",
                        url, result.status_code, (last_hash or "")[:12],
                    )
                    await _log_entry(
                        db,
                        execution_id=eid,
                        step_name="scrape_url",
                        status="NO_CHANGE",
                        details={"url": url, "depth": depth, "status_code": result.status_code},
                        duration_ms=dur,
                        source_id=src["id"],
                    )
                else:
                    source_new += 1
                    scraped += 1
                    logger.info(
                        "  │  SCRAPED    url=%-55s  status=%d  hash=%s  dur=%dms",
                        url, result.status_code,
                        (result.content_hash or "")[:12], dur,
                    )

                    # Update root source metadata when root URL is fetched
                    if url == root_url:
                        await db.execute(
                            text(
                                "UPDATE tenant_sources "
                                "SET last_scraped_at=:now, last_etag=:etag, last_content_hash=:hash "
                                "WHERE id=:sid"
                            ),
                            {
                                "now":  _now(),
                                "etag": getattr(result, "etag", None),
                                "hash": result.content_hash,
                                "sid":  uuid.UUID(src["id"]),
                            },
                        )

                    hero_img = pick_primary_image_url(result.images)
                    media_blob = compact_media_payload(
                        result.images, result.videos, result.audio
                    )

                    raw_items.append({
                        "source_id":      src["id"],
                        "url":            url,
                        "raw_html":       result.raw_html,
                        "clean_text":     result.clean_text,
                        "title":          result.title,
                        "fetched_at":     _now().isoformat(),
                        "content_hash":   result.content_hash,
                        "depth":          depth,
                        "visit_strategy": visit_strategy,
                        "min_text_chars": int(src.get("min_text_chars", 40)),
                        "require_title":  bool(src.get("require_title", True)),
                        "img_url":        hero_img,
                        "media_refs":     media_blob,
                    })

                    await _log_entry(
                        db,
                        execution_id=eid,
                        step_name="scrape_url",
                        status="SUCCESS",
                        details={
                            "url": url,
                            "depth": depth,
                            "status_code": result.status_code,
                            "visit_strategy": visit_strategy,
                        },
                        duration_ms=dur,
                        source_id=src["id"],
                    )

                # ── Discover child links if we have depth budget left ──────────
                if depth < max_depth and not result.error:
                    try:
                        links_result = await scraper.fetch_links(
                            url=url,
                            same_domain_only=same_domain_only,
                            include_patterns=include_patterns if include_patterns else None,
                        )
                        if links_result.error:
                            logger.warning(
                                "  │    fetch_links error  url=%s  %s",
                                url,
                                links_result.error,
                            )
                        else:
                            # Filter: not in visited (in-memory guard), not excluded,
                            # then cap to max_child_links per URL (per-page branching).
                            # links_result.links is list[LinkItem] — extract .href strings.
                            raw_link_count = len(links_result.links)
                            candidates = [
                                link.href
                                for link in links_result.links
                                if link.href not in visited
                                and not _url_matches_exclude(link.href, exclude_patterns)
                            ]
                            capped = candidates[:max_child_links]
                            new_children = 0
                            for child_url in capped:
                                if len(visited) >= max_links_per_source:
                                    break
                                visited.add(child_url)
                                queue.append((child_url, depth + 1))
                                new_children += 1
                            logger.info(
                                "  │    links raw=%d  after_exclude=%d  per_url_cap=%d  "
                                "queued=%d  depth=%d→%d  visited=%d/%d",
                                raw_link_count,
                                len(candidates),
                                max_child_links,
                                new_children,
                                depth,
                                depth + 1,
                                len(visited),
                                max_links_per_source,
                            )
                    except Exception as exc:
                        logger.warning(
                            "  │    fetch_links failed for %s: %s", url, exc
                        )

            logger.info(
                "  └─ source done  url=%-55s  fetched=%d  skipped=%d  visited=%d  strategy=%s",
                root_url, source_new, source_skipped, len(visited), visit_strategy,
            )

        await db.commit()

    logger.info(
        "  scrape summary  total_scraped=%d  total_skipped=%d  total_urls_visited=%d",
        scraped, skipped, total_urls,
    )
    return {
        "raw_items":       raw_items,
        "sources_scraped": scraped,
        "sources_skipped": skipped,
        "urls_visited":    total_urls,
    }


# ── Node 3: normalize_articles ─────────────────────────────────────────────────

async def normalize_articles(state: IngestionState) -> dict:
    eid  = state["execution_id"]
    tid  = uuid.UUID(state["tenant_id"])
    articles: list[dict] = []
    new_count = 0
    duplicate_count = 0
    empty_text_count = 0
    skipped_title_count = 0

    logger.info(_SEP)
    logger.info(
        "NODE 3 / normalize_articles  |  raw_items=%d  execution_id=%s",
        len(state.get("raw_items", [])), eid,
    )

    async with get_tenant_db_session(tid) as db:
        for item in state.get("raw_items", []):
            t0 = time.monotonic()

            # Prefer crawl4ai clean_text; fall back to trafilatura on raw HTML
            min_chars = int(item.get("min_text_chars", 40))
            req_title = bool(item.get("require_title", True))

            if item.get("clean_text"):
                text_body = item["clean_text"]
                title     = item.get("title") or item["url"]
            else:
                extracted = html_to_text(item.get("raw_html", ""), url=item["url"])
                text_body = extracted["text"]
                title     = extracted["title"] or item["url"]

            if req_title and not (title or "").strip():
                skipped_title_count += 1
                logger.info(
                    "  SKIP article (require_title, empty title)  url=%s  depth=%d",
                    item["url"],
                    item.get("depth", 0),
                )
                continue

            if not text_body or len(text_body.strip()) < min_chars:
                empty_text_count += 1
                logger.info(
                    "  SKIP article (short text)  url=%s  depth=%d  text_len=%d  min=%d",
                    item["url"],
                    item.get("depth", 0),
                    len((text_body or "").strip()),
                    min_chars,
                )
                continue

            article = NormalizedArticle(
                source_id=uuid.UUID(item["source_id"]),
                url=item["url"],
                title=title,
                text=text_body,
                img_url=item.get("img_url"),
                # summary populated in tag_and_format for cost reasons
            )

            result = await db.execute(
                text(
                    """
                    INSERT INTO articles
                        (id, source_id, url, title, text, img_url, created_at)
                    VALUES (:id, :sid, :url, :title, :text, :img, :now)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id":    article.id,
                    "sid":   article.source_id,
                    "url":   article.url,
                    "title": article.title,
                    "text":  article.text,
                    "img":   article.img_url,
                    "now":   _now(),
                },
            )
            inserted = result.fetchone()
            if inserted:
                new_count += 1
                dur = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "  NEW article  id=%s  title=%-50s  depth=%d  dur=%dms",
                    str(article.id)[:8], article.title[:50], item.get("depth", 0), dur,
                )
                await _log_entry(
                    db,
                    execution_id=eid,
                    step_name="normalize_article",
                    status="SUCCESS",
                    details={
                        "url":   article.url,
                        "title": article.title[:80],
                        "depth": item.get("depth", 0),
                    },
                    duration_ms=dur,
                    source_id=item["source_id"],
                    article_id=str(article.id),
                )
                articles.append({
                    "id":        str(article.id),
                    "source_id": item["source_id"],
                    "url":       article.url,
                    "title":     article.title,
                    "text":      article.text,
                    "img_url":   article.img_url,
                })
            else:
                duplicate_count += 1
                logger.info("  DUPLICATE (skipped)  url=%s", item["url"])

        await db.commit()

    logger.info(
        "  normalize done  new=%d  dup=%d  short_text=%d  no_title=%d  raw_items=%d",
        new_count,
        duplicate_count,
        empty_text_count,
        skipped_title_count,
        len(state.get("raw_items", [])),
    )
    return {"articles": articles, "new_articles": new_count}


# ── Node 4: tag_and_format_articles ────────────────────────────────────────────

_TAGGING_PROMPT = """\
You are a precise content tagger. Given an article and a candidate tag list,
return ONLY a JSON object with:
  "tags": [up to 6 most relevant tags drawn EXCLUSIVELY from the candidates]
  "confidences": {{tag: float 0-1}}

Persona context (align tags with this AI coworker's identity):
{persona_block}

Candidate tags: {tags}
Article title: {title}
Article text (first 1200 chars): {text}

Respond with valid JSON only. No markdown fences. No extra keys."""


async def tag_and_format_articles(state: IngestionState) -> dict:
    eid      = state["execution_id"]
    tid      = uuid.UUID(state["tenant_id"])
    policy   = state.get("effective_policy", {})
    articles = state.get("articles", [])

    tagging_blocked          = _is_capability_blocked("tag_content_item", policy)
    formatting_needs_approval = _requires_approval("apply_article_format_template", policy)
    budget_limit             = _budget_limit(policy)

    logger.info(_SEP)
    logger.info(
        "NODE 4 / tag_and_format_articles  |  articles=%d  budget=$%.2f  execution_id=%s",
        len(articles), budget_limit, eid,
    )
    if tagging_blocked:
        logger.warning(
            "  POLICY: tag_content_item is BLOCKED for this tenant "
            "— skipping LLM tagging for all %d articles.",
            len(articles),
        )
    if formatting_needs_approval:
        logger.warning(
            "  POLICY: apply_article_format_template REQUIRES HUMAN APPROVAL "
            "(proceeding for PoC — flagging in log).",
        )

    if not articles:
        logger.info("  No new articles to tag/format — skipping node.")
        return {"total_tokens_in": 0, "total_tokens_out": 0, "estimated_cost_usd": 0.0}

    tokens_in  = tokens_out = 0
    cost       = 0.0
    running_cost = state.get("estimated_cost_usd", 0.0)

    async with get_tenant_db_session(tid) as db:
        for article in articles:
            # ── Budget guard ─────────────────────────────────────────────────
            if running_cost + cost >= budget_limit:
                logger.warning(
                    "  BUDGET LIMIT REACHED  limit=$%.4f  accumulated=$%.6f  "
                    "— stopping tag loop early.",
                    budget_limit, running_cost + cost,
                )
                await _log_entry(
                    db,
                    execution_id=eid,
                    step_name="tag_and_format_article",
                    status="ERROR",
                    details={"reason": "budget_exceeded", "limit_usd": budget_limit},
                )
                break

            t0         = time.monotonic()
            raw_tags: list[str]             = []
            confidences: dict[str, float]   = {}
            ti = to = 0

            # ── tag_content_item ─────────────────────────────────────────────
            if not tagging_blocked:
                ps = state.get("persona_summary") or ""
                pdict = state.get("persona") or {}
                persona_block = (
                    f"{ps}\n"
                    f"Role: {pdict.get('role_description', '')}\n"
                    f"Tone: {pdict.get('tone_style', '')}"
                ).strip()
                if not persona_block:
                    persona_block = (
                        "Generic ingestion coworker — tag for relevance to the taxonomy."
                    )
                prompt = _TAGGING_PROMPT.format(
                    persona_block=persona_block,
                    tags=", ".join(state.get("tag_taxonomy", [])),
                    title=article["title"],
                    text=article["text"][:1200],
                )
                try:
                    response = await llm.ainvoke(prompt, config=graph_runnable_config())
                    usage     = getattr(response, "usage_metadata", None) or {}
                    ti        = usage.get("input_tokens", 0)
                    to        = usage.get("output_tokens", 0)
                    tokens_in  += ti
                    tokens_out += to
                    cost       += ti * _COST_IN + to * _COST_OUT
                    running_cost += ti * _COST_IN + to * _COST_OUT

                    parsed: dict = json.loads(response.content)
                    raw_tags     = parsed.get("tags", [])[:6]
                    confidences  = parsed.get("confidences", {})

                    logger.info(
                        "  TAGGED  id=%s  tags=%s  tokens=%d+%d  cost=$%.6f",
                        str(article["id"])[:8], raw_tags, ti, to,
                        ti * _COST_IN + to * _COST_OUT,
                    )
                except Exception as exc:
                    logger.warning(
                        "  tagging error for article=%s: %s",
                        article["id"], exc,
                    )

                # Persist article_tags
                for tag_name in raw_tags:
                    tag_row = await db.execute(
                        text("SELECT id FROM tenant_tags WHERE name = :name"),
                        {"name": tag_name},
                    )
                    tag = tag_row.fetchone()
                    if tag:
                        await db.execute(
                            text(
                                "INSERT INTO article_tags (article_id, tag_id, confidence) "
                                "VALUES (:aid, :tagid, :conf) ON CONFLICT DO NOTHING"
                            ),
                            {
                                "aid":   uuid.UUID(article["id"]),
                                "tagid": tag.id,
                                "conf":  confidences.get(tag_name, 1.0),
                            },
                        )

            # ── apply_article_format_template ────────────────────────────────
            fmt_id = state.get("format_template_id")
            if fmt_id:
                formatted: dict[str, Any] = {
                    "title":                    article["title"],
                    "text":                     article["text"],
                    "url":                      article["url"],
                    "primary_tag":              raw_tags[0] if raw_tags else None,
                    "secondary_tags":           raw_tags[1:],
                    "tagging_skipped":          tagging_blocked,
                    "requires_approval":        formatting_needs_approval,
                }
                await db.execute(
                    text(
                        """
                        INSERT INTO formatted_articles
                            (article_id, format_template_id, formatted_json, created_at)
                        VALUES (:aid, :fid, CAST(:json AS jsonb), :now)
                        ON CONFLICT (article_id) DO NOTHING
                        """
                    ),
                    {
                        "aid":  uuid.UUID(article["id"]),
                        "fid":  uuid.UUID(fmt_id),
                        "json": json.dumps(formatted),
                        "now":  _now(),
                    },
                )
                logger.info(
                    "  FORMATTED  id=%s  primary_tag=%s  approval_needed=%s",
                    str(article["id"])[:8],
                    formatted["primary_tag"],
                    formatting_needs_approval,
                )

            entry_cost = ti * _COST_IN + to * _COST_OUT
            await _log_entry(
                db,
                execution_id=eid,
                step_name="tag_and_format_article",
                status="SUCCESS",
                details={
                    "tags":                    raw_tags,
                    "tagging_skipped":         tagging_blocked,
                    "formatting_needs_approval": formatting_needs_approval,
                    "url":                     article["url"],
                },
                tokens_in=ti,
                tokens_out=to,
                cost_usd=entry_cost,
                duration_ms=int((time.monotonic() - t0) * 1000),
                source_id=article["source_id"],
                article_id=article["id"],
            )

        await db.commit()

    total_in   = (state.get("total_tokens_in")  or 0) + tokens_in
    total_out  = (state.get("total_tokens_out") or 0) + tokens_out
    total_cost = (state.get("estimated_cost_usd") or 0.0) + cost

    logger.info(
        "  tag/format done  tokens=%d+%d  node_cost=$%.6f  cumulative=$%.6f  budget=$%.2f",
        tokens_in, tokens_out, cost, total_cost, budget_limit,
    )
    return {
        "total_tokens_in":    total_in,
        "total_tokens_out":   total_out,
        "estimated_cost_usd": total_cost,
    }


# ── Node 5: summarize_execution ────────────────────────────────────────────────

async def summarize_execution(state: IngestionState) -> dict:
    eid    = state["execution_id"]
    tid    = uuid.UUID(state["tenant_id"])
    policy = state.get("effective_policy", {})

    logger.info(_SEP)
    logger.info(
        "NODE 5 / summarize_execution  |  execution_id=%s", eid
    )

    summary = {
        "tenant_id":           state["tenant_id"],
        "execution_id":        eid,
        "persona_id":          state.get("persona_id"),
        "persona_display_name": (state.get("persona") or {}).get("display_name"),
        "persona_summary":     state.get("persona_summary"),
        "sources_scraped":     state.get("sources_scraped", 0),
        "sources_skipped":     state.get("sources_skipped", 0),
        "urls_visited":        state.get("urls_visited", 0),
        "new_articles":        state.get("new_articles", 0),
        "total_tokens_in":     state.get("total_tokens_in", 0),
        "total_tokens_out":    state.get("total_tokens_out", 0),
        "estimated_cost_usd":  round(state.get("estimated_cost_usd", 0.0), 6),
        "policy_applied": {
            "tagging_blocked":             _is_capability_blocked("tag_content_item", policy),
            "formatting_needs_approval":   _requires_approval("apply_article_format_template", policy),
            "budget_limit_usd":            _budget_limit(policy),
        },
    }

    async with get_tenant_db_session(tid) as db:
        await db.execute(
            text(
                """
                UPDATE ingestion_executions
                SET finished_at  = :now,
                    status       = 'SUCCESS',
                    summary_json = CAST(:summary AS jsonb)
                WHERE execution_id = :eid
                """
            ),
            {
                "now":     _now(),
                "summary": json.dumps(summary),
                "eid":     uuid.UUID(eid),
            },
        )
        await db.commit()

    logger.info(_SEP)
    logger.info("EXECUTION COMPLETE  |  execution_id=%s", eid)
    logger.info(
        "  sources_scraped=%-4d  sources_skipped=%-4d  urls_visited=%-4d",
        summary["sources_scraped"],
        summary["sources_skipped"],
        summary["urls_visited"],
    )
    logger.info(
        "  new_articles=%-6d  tokens=%d+%d  cost=$%.6f  budget=$%.2f",
        summary["new_articles"],
        summary["total_tokens_in"],
        summary["total_tokens_out"],
        summary["estimated_cost_usd"],
        summary["policy_applied"]["budget_limit_usd"],
    )
    logger.info(
        "  policy  tagging_blocked=%-5s  approval_needed=%-5s",
        summary["policy_applied"]["tagging_blocked"],
        summary["policy_applied"]["formatting_needs_approval"],
    )
    logger.info(_SEP)
    return {"summary": summary}


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_ingestion_graph() -> Any:
    g: StateGraph = StateGraph(IngestionState)
    g.add_node("load_tenant_config",         load_tenant_config)
    g.add_node("scrape_sources_incremental", scrape_sources_incremental)
    g.add_node("normalize_articles",         normalize_articles)
    g.add_node("tag_and_format_articles",    tag_and_format_articles)
    g.add_node("summarize_execution",        summarize_execution)

    g.set_entry_point("load_tenant_config")
    g.add_edge("load_tenant_config",         "scrape_sources_incremental")
    g.add_edge("scrape_sources_incremental", "normalize_articles")
    g.add_edge("normalize_articles",         "tag_and_format_articles")
    g.add_edge("tag_and_format_articles",    "summarize_execution")
    g.add_edge("summarize_execution",        END)
    return g.compile()


_graph = build_ingestion_graph()


# ── Public entrypoint ──────────────────────────────────────────────────────────

async def run_ingestion_graph(
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    effective_policy: dict | None = None,
    persona_id: uuid.UUID | None = None,
    persona: dict | None = None,
    persona_summary: str | None = None,
) -> dict:
    """
    Run the full ingestion pipeline for one tenant.

    The tenant DB is resolved automatically via tenant_db_connections in the
    control plane DB.  All tenant-specific reads/writes go to the tenant's own DB.
    """
    logger.info(_SEP)
    logger.info(
        "INGESTION START  |  tenant_id=%s  execution_id=%s  persona_id=%s",
        tenant_id, execution_id, persona_id,
    )

    initial_state: IngestionState = {
        "tenant_id":           str(tenant_id),
        "execution_id":        str(execution_id),
        "effective_policy":    effective_policy or {},
        "persona_id":          str(persona_id) if persona_id else None,
        "persona":             persona,
        "persona_summary":     persona_summary,
        "sources":             [],
        "tag_taxonomy":        [],
        "format_template":     {},
        "format_template_id":  None,
        "raw_items":           [],
        "sources_scraped":     0,
        "sources_skipped":     0,
        "urls_visited":        0,
        "articles":            [],
        "new_articles":        0,
        "total_tokens_in":     0,
        "total_tokens_out":    0,
        "estimated_cost_usd":  0.0,
        "summary":             {},
        "error":               None,
    }
    with langfuse_trace(
        tenant_id=tenant_id,
        execution_id=execution_id,
        service="content_ingestion",
        skill_id="content_ingestion",
    ) as lf_cfg:
        final_state: IngestionState = await _graph.ainvoke(initial_state, config=lf_cfg)
    return final_state.get("summary", {})
