"""
LangGraph content curation pipeline.

Nodes:
  1. load_config        → tenant sources, products, tags, persona
  2. scrape_sources     → BFS web scraping (reuses scraper MCP tool)
  3. extract_content    → normalize raw HTML into structured articles
  4. match_products     → LLM matches articles to tenant products
  5. generate_newsletter → LLM generates newsletter-ready articles with product refs
  6. save_results       → persist to tenant DB (newsletter_articles, executions)

DB: all reads/writes go to the tenant's own DB (no tenant_id columns).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from sqlalchemy import text

from app.core.config import settings
from app.core.langfuse_setup import graph_runnable_config, langfuse_trace
from app.core.tenant_db import get_tenant_db_session
from tools.scraper_mcp.client import ScraperMCPClient
from tools.scraper_mcp.helpers import (
    compact_media_payload,
    content_hash,
    html_to_text,
    pick_primary_image_url,
)

logger = logging.getLogger(__name__)

_COST_IN = 0.15 / 1_000_000
_COST_OUT = 0.60 / 1_000_000

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


# ── Graph State ────────────────────────────────────────────────────────────────

class CurationState(TypedDict, total=False):
    tenant_id: str
    execution_id: str
    goal: str
    effective_policy: dict
    persona_id: str | None
    persona: dict | None
    persona_summary: str | None
    # Node 1: config
    sources: list[dict]
    products: list[dict]
    tag_taxonomy: list[str]
    # Node 2: scrape
    scraped_pages: list[dict]
    applied_scraping_configs: list[dict]
    sources_scraped: int
    pages_fetched: int
    # Node 3: extract
    articles: list[dict]
    articles_extracted: int
    # Node 4: product matching
    article_products: list[dict]  # [{article_id, product_matches: [...]}]
    products_matched: int
    # Node 5: newsletter
    newsletter_articles: list[dict]
    newsletter_count: int
    # Accumulated cost
    total_tokens_in: int
    total_tokens_out: int
    estimated_cost_usd: float
    # Final
    summary: dict
    error: str | None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _budget_limit(policy: dict) -> float:
    return float(policy.get("budget", {}).get("perExecutionUsdLimit", 1.0))


# ── Node 1: load_config ───────────────────────────────────────────────────────

async def load_config(state: CurationState) -> dict:
    tid = uuid.UUID(state["tenant_id"])
    t0 = time.monotonic()

    logger.info(_SEP)
    logger.info("NODE 1 / load_config  |  tenant=%s", state["tenant_id"])

    async with get_tenant_db_session(tid) as db:
        src_rows = await db.execute(
            text(
                "SELECT id, url, type, max_depth, same_domain_only, include_patterns, "
                "       max_child_links_per_page, max_links_to_scrape, "
                "       exclude_patterns, min_text_chars, require_title, "
                "       last_content_hash "
                "FROM tenant_sources WHERE active = true ORDER BY url"
            )
        )
        sources = [
            {
                "id": str(r.id),
                "url": r.url,
                "type": r.type,
                "max_depth": r.max_depth,
                "same_domain_only": r.same_domain_only,
                "include_patterns": list(r.include_patterns or []),
                "exclude_patterns": list(r.exclude_patterns or []),
                "max_child_links_per_page": r.max_child_links_per_page,
                "max_links_to_scrape": r.max_links_to_scrape,
                "min_text_chars": r.min_text_chars,
                "require_title": r.require_title,
                "last_content_hash": r.last_content_hash,
            }
            for r in src_rows.fetchall()
        ]

        prod_rows = await db.execute(
            text(
                "SELECT id, name, description, url, category, tags, features "
                "FROM tenant_products WHERE active = true ORDER BY name"
            )
        )
        products = [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "url": r.url,
                "category": r.category,
                "tags": list(r.tags or []),
                "features": list(r.features or []),
            }
            for r in prod_rows.fetchall()
        ]

        tag_rows = await db.execute(
            text("SELECT name FROM tenant_tags ORDER BY name")
        )
        taxonomy = [r.name for r in tag_rows.fetchall()]

    dur = int((time.monotonic() - t0) * 1000)
    logger.info(
        "  sources=%d  products=%d  tags=%d  dur=%dms",
        len(sources), len(products), len(taxonomy), dur,
    )
    return {
        "sources": sources,
        "products": products,
        "tag_taxonomy": taxonomy,
    }


# ── Node 2: scrape_sources ────────────────────────────────────────────────────

async def scrape_sources(state: CurationState) -> dict:
    logger.info(_SEP)
    logger.info("NODE 2 / scrape_sources  |  sources=%d", len(state.get("sources", [])))

    tid = uuid.UUID(state["tenant_id"])
    scraped_pages: list[dict] = []
    applied_scraping_configs: list[dict] = []
    total_scraped = 0
    total_fetched = 0

    policy_limits = (state.get("effective_policy") or {}).get("scraping_limits", {})

    async with get_tenant_db_session(tid) as db:
        for src in state.get("sources", []):
            root_url = src["url"]
            max_depth = int(src.get("max_depth", 1))
            same_domain = src.get("same_domain_only", True)
            include_pats = src.get("include_patterns", [])
            exclude_pats = list(src.get("exclude_patterns") or [])
            max_child = int(src.get("max_child_links_per_page", 4))
            max_total = int(src.get("max_links_to_scrape", 25))

            # Source of truth: tenant DB source settings.
            # Policy-level limits act only as fallback/defaults for fields not in tenant_sources.
            scraping_config: dict[str, Any] = {
                **policy_limits,
                "max_depth": max_depth,
                "max_links_per_page": max_child,
                "max_total_links": max_total,
                "allow_external_domains": not same_domain,
            }
            applied_scraping_configs.append(
                {
                    "source_id": src["id"],
                    "source_url": root_url,
                    "scraping_config": scraping_config,
                }
            )
            try:
                crawl_result = await scraper.deep_crawl(
                    seed_url=root_url,
                    strategy="bfs",
                    max_depth=max_depth,
                    max_pages=max_total,
                    include_external=not same_domain,
                    include_patterns=include_pats if include_pats else None,
                    exclude_patterns=exclude_pats if exclude_pats else None,
                    include_media=True,
                    scraping_config=scraping_config,
                )
            except Exception as exc:
                logger.warning("  DEEP_CRAWL_EXCEPTION  source=%s  %s", root_url, exc)
                continue
            if crawl_result.error:
                logger.warning("  DEEP_CRAWL_ERROR  source=%s  %s", root_url, crawl_result.error)
                continue

            total_fetched += len(crawl_result.pages)

            root_hash: str | None = None
            for page in crawl_result.pages[:max_total]:
                if page.error or page.status_code in (0, 304):
                    continue
                if not (page.clean_text or "").strip():
                    continue

                total_scraped += 1
                page_depth = int(getattr(page, "depth", 0) or 0)
                page_hash = content_hash(page.clean_text)
                if page.url == root_url:
                    root_hash = page_hash

                hero = pick_primary_image_url(page.images)
                media_refs = compact_media_payload(page.images, page.videos, page.audio)

                scraped_pages.append({
                    "source_id": src["id"],
                    "url": page.url,
                    "raw_html": "",
                    "clean_text": page.clean_text,
                    "title": page.title,
                    "content_hash": page_hash,
                    "depth": page_depth,
                    "min_text_chars": src.get("min_text_chars", 40),
                    "require_title": src.get("require_title", True),
                    "img_url": hero,
                    "media_refs": media_refs,
                })

            if root_hash:
                await db.execute(
                    text(
                        "UPDATE tenant_sources "
                        "SET last_scraped_at=:now, last_content_hash=:hash "
                        "WHERE id=:sid"
                    ),
                    {
                        "now": _now(),
                        "hash": root_hash,
                        "sid": uuid.UUID(src["id"]),
                    },
                )

        await db.commit()

    logger.info(
        "  scrape done  scraped=%d  fetched=%d  pages_collected=%d",
        total_scraped, total_fetched, len(scraped_pages),
    )
    return {
        "scraped_pages": scraped_pages,
        "applied_scraping_configs": applied_scraping_configs,
        "sources_scraped": total_scraped,
        "pages_fetched": total_fetched,
    }


# ── Node 3: extract_content ───────────────────────────────────────────────────

async def extract_content(state: CurationState) -> dict:
    logger.info(_SEP)
    logger.info("NODE 3 / extract_content  |  pages=%d", len(state.get("scraped_pages", [])))

    tid = uuid.UUID(state["tenant_id"])
    articles: list[dict] = []
    new_count = 0

    async with get_tenant_db_session(tid) as db:
        for page in state.get("scraped_pages", []):
            min_chars = int(page.get("min_text_chars", 40))
            req_title = bool(page.get("require_title", True))

            if page.get("clean_text"):
                text_body = page["clean_text"]
                title = page.get("title") or page["url"]
            else:
                extracted = html_to_text(page.get("raw_html", ""), url=page["url"])
                text_body = extracted["text"]
                title = extracted["title"] or page["url"]

            if req_title and not (title or "").strip():
                continue
            if not text_body or len(text_body.strip()) < min_chars:
                continue

            article_id = uuid.uuid4()
            img_url = page.get("img_url")

            # Matcher reads `articles` from this node only. DO NOTHING skips URLs already in DB,
            # so repeat runs scraped pages but matched nothing — upsert + RETURNING fixes that.
            existed = await db.execute(
                text("SELECT 1 FROM articles WHERE url = :url LIMIT 1"),
                {"url": page["url"]},
            )
            is_new_url = existed.fetchone() is None

            result = await db.execute(
                text(
                    "INSERT INTO articles (id, source_id, url, title, text, img_url, created_at) "
                    "VALUES (:id, :sid, :url, :title, :text, :img, :now) "
                    "ON CONFLICT (url) DO UPDATE SET "
                    "  source_id = EXCLUDED.source_id, "
                    "  title = EXCLUDED.title, "
                    "  text = EXCLUDED.text, "
                    "  img_url = COALESCE(EXCLUDED.img_url, articles.img_url) "
                    "RETURNING id"
                ),
                {
                    "id": article_id,
                    "sid": uuid.UUID(page["source_id"]),
                    "url": page["url"],
                    "title": title,
                    "text": text_body,
                    "img": img_url,
                    "now": _now(),
                },
            )
            row = result.fetchone()
            if row:
                if is_new_url:
                    new_count += 1
                articles.append({
                    "id": str(row.id),
                    "source_id": page["source_id"],
                    "url": page["url"],
                    "title": title,
                    "text": text_body[:3000],
                    "img_url": img_url,
                    "media_refs": page.get("media_refs") or {},
                })

        await db.commit()

    logger.info(
        "  extracted %d new rows, %d articles for matcher (from %d pages)",
        new_count,
        len(articles),
        len(state.get("scraped_pages", [])),
    )
    return {"articles": articles, "articles_extracted": new_count}


# ── Node 4: match_products ─────────────────────────────────────────────────────

_PRODUCT_MATCH_PROMPT = """\
You are a product-content matching engine for a B2B content curation platform.

Given an ARTICLE and a list of PRODUCTS, identify which products are relevant
to the article's topic. Return a JSON array of matches.

PERSONA CONTEXT:
{persona_block}

ARTICLE:
Title: {title}
Summary (first 800 chars): {text}

PRODUCTS:
{products_block}

Return ONLY a valid JSON array. Each element:
{{"product_id": "...", "product_name": "...", "relevance_score": 0.0-1.0, "match_reason": "brief reason"}}

If no products match, return an empty array [].
No markdown fences. No extra text."""


async def match_products(state: CurationState) -> dict:
    logger.info(_SEP)
    logger.info("NODE 4 / match_products  |  articles=%d  products=%d",
                len(state.get("articles", [])), len(state.get("products", [])))

    articles = state.get("articles", [])
    products = state.get("products", [])
    policy = state.get("effective_policy", {})
    budget = _budget_limit(policy)

    if not articles or not products:
        logger.info("  No articles or products to match — skipping.")
        return {"article_products": [], "products_matched": 0}

    products_block = "\n".join(
        f"- ID: {p['id']}, Name: {p['name']}, "
        f"Category: {p.get('category', '')}, "
        f"Description: {p.get('description', '')[:200]}"
        for p in products
    )

    ps = state.get("persona_summary") or "Generic content curator"
    persona_block = ps

    article_products: list[dict] = []
    total_matched = 0
    tokens_in = tokens_out = 0
    cost = state.get("estimated_cost_usd", 0.0)

    for article in articles:
        if cost >= budget:
            logger.warning("  Budget limit reached ($%.4f >= $%.4f)", cost, budget)
            break

        prompt = _PRODUCT_MATCH_PROMPT.format(
            persona_block=persona_block,
            title=article["title"],
            text=article["text"][:800],
            products_block=products_block,
        )

        try:
            response = await llm.ainvoke(prompt, config=graph_runnable_config())
            usage = getattr(response, "usage_metadata", None) or {}
            ti = usage.get("input_tokens", 0)
            to = usage.get("output_tokens", 0)
            tokens_in += ti
            tokens_out += to
            step_cost = ti * _COST_IN + to * _COST_OUT
            cost += step_cost

            content = response.content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(l for l in lines if not l.strip().startswith("```"))

            matches = json.loads(content)
            if matches:
                total_matched += len(matches)
                article_products.append({
                    "article_id": article["id"],
                    "article_title": article["title"],
                    "article_url": article["url"],
                    "article_text": article["text"],
                    "article_img_url": article.get("img_url"),
                    "media_refs": article.get("media_refs") or {},
                    "product_matches": matches,
                })
                logger.info(
                    "  MATCHED  article=%s  products=%d  cost=$%.6f",
                    article["title"][:40], len(matches), step_cost,
                )
        except Exception as exc:
            logger.warning("  Product matching error for article=%s: %s", article["id"], exc)

    prev_in = state.get("total_tokens_in", 0)
    prev_out = state.get("total_tokens_out", 0)
    prev_cost = state.get("estimated_cost_usd", 0.0)

    logger.info(
        "  match done  articles_with_products=%d  total_matches=%d",
        len(article_products), total_matched,
    )
    return {
        "article_products": article_products,
        "products_matched": total_matched,
        "total_tokens_in": prev_in + tokens_in,
        "total_tokens_out": prev_out + tokens_out,
        "estimated_cost_usd": prev_cost + (cost - state.get("estimated_cost_usd", 0.0)),
    }


# ── Node 5: generate_newsletter ───────────────────────────────────────────────

_NEWSLETTER_PROMPT = """\
You are a content curation AI coworker creating newsletter articles for a B2B audience.

PERSONA:
{persona_block}

SOURCE ARTICLE:
Title: {title}
URL: {url}
Content (first 1200 chars): {text}

MATCHED PRODUCTS (reference these naturally in the newsletter article):
{products_block}

Write a newsletter-ready article that:
1. Summarizes the key insights from the source article.
2. Naturally references relevant products/services where they add value.
3. Is engaging, concise (200-400 words), and ready for LinkedIn/social media.
4. Includes a clear headline.

Return ONLY valid JSON:
{{"title": "...", "summary": "2-3 sentence summary", "body": "full newsletter text", "tags": ["tag1", "tag2"]}}

No markdown fences. No extra text."""


async def generate_newsletter(state: CurationState) -> dict:
    logger.info(_SEP)
    logger.info("NODE 5 / generate_newsletter  |  article_products=%d",
                len(state.get("article_products", [])))

    article_products = state.get("article_products", [])
    policy = state.get("effective_policy", {})
    budget = _budget_limit(policy)

    if not article_products:
        logger.info("  No articles with product matches — skipping newsletter generation.")
        return {"newsletter_articles": [], "newsletter_count": 0}

    ps = state.get("persona_summary") or "Generic content curator"
    pdict = state.get("persona") or {}
    persona_block = (
        f"{ps}\n"
        f"Role: {pdict.get('role_description', '')}\n"
        f"Tone: {pdict.get('tone_style', '')}"
    ).strip()

    newsletters: list[dict] = []
    tokens_in = tokens_out = 0
    cost = state.get("estimated_cost_usd", 0.0)

    for ap in article_products:
        if cost >= budget:
            logger.warning("  Budget limit reached during newsletter generation")
            break

        products_block = "\n".join(
            f"- {m['product_name']}: {m.get('match_reason', '')}"
            for m in ap["product_matches"]
        )

        prompt = _NEWSLETTER_PROMPT.format(
            persona_block=persona_block,
            title=ap["article_title"],
            url=ap["article_url"],
            text=ap.get("article_text", "")[:1200],
            products_block=products_block,
        )

        try:
            response = await llm.ainvoke(prompt, config=graph_runnable_config())
            usage = getattr(response, "usage_metadata", None) or {}
            ti = usage.get("input_tokens", 0)
            to = usage.get("output_tokens", 0)
            tokens_in += ti
            tokens_out += to
            step_cost = ti * _COST_IN + to * _COST_OUT
            cost += step_cost

            content = response.content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(l for l in lines if not l.strip().startswith("```"))

            parsed = json.loads(content)

            newsletter = {
                "id": str(uuid.uuid4()),
                "article_id": ap["article_id"],
                "title": parsed.get("title", ap["article_title"]),
                "summary": parsed.get("summary", ""),
                "body": parsed.get("body", ""),
                "product_refs": ap["product_matches"],
                "tags": parsed.get("tags", []),
                "source_url": ap["article_url"],
                "img_url": ap.get("article_img_url"),
                "media_refs": ap.get("media_refs") or {},
                "status": "draft",
            }
            newsletters.append(newsletter)
            logger.info(
                "  NEWSLETTER  title=%.50s  products=%d  cost=$%.6f",
                newsletter["title"], len(ap["product_matches"]), step_cost,
            )
        except Exception as exc:
            logger.warning("  Newsletter generation error: %s", exc)

    prev_in = state.get("total_tokens_in", 0)
    prev_out = state.get("total_tokens_out", 0)
    prev_cost = state.get("estimated_cost_usd", 0.0)

    logger.info("  newsletter done  created=%d", len(newsletters))
    return {
        "newsletter_articles": newsletters,
        "newsletter_count": len(newsletters),
        "total_tokens_in": prev_in + tokens_in,
        "total_tokens_out": prev_out + tokens_out,
        "estimated_cost_usd": prev_cost + (cost - state.get("estimated_cost_usd", 0.0)),
    }


# ── Node 6: save_results ──────────────────────────────────────────────────────

async def save_results(state: CurationState) -> dict:
    logger.info(_SEP)
    logger.info("NODE 6 / save_results  |  newsletters=%d", len(state.get("newsletter_articles", [])))

    tid = uuid.UUID(state["tenant_id"])
    eid = state["execution_id"]

    summary = {
        "tenant_id": state["tenant_id"],
        "execution_id": eid,
        "persona_id": state.get("persona_id"),
        "goal": state.get("goal", ""),
        "sources_scraped": state.get("sources_scraped", 0),
        "pages_fetched": state.get("pages_fetched", 0),
        "articles_extracted": state.get("articles_extracted", 0),
        "newsletter_articles_created": state.get("newsletter_count", 0),
        "products_matched": state.get("products_matched", 0),
        "applied_scraping_configs": state.get("applied_scraping_configs", []),
        "total_tokens_in": state.get("total_tokens_in", 0),
        "total_tokens_out": state.get("total_tokens_out", 0),
        "estimated_cost_usd": round(state.get("estimated_cost_usd", 0.0), 6),
    }

    async with get_tenant_db_session(tid) as db:
        # Save execution record
        await db.execute(
            text(
                "INSERT INTO executions (execution_id, skill_id, persona_id, goal, "
                "                        started_at, finished_at, status, result_json, cost_json) "
                "VALUES (:eid, 'content_curation', :pid, :goal, :start, :finish, 'SUCCESS', "
                "        CAST(:result AS jsonb), CAST(:cost AS jsonb)) "
                "ON CONFLICT (execution_id) DO UPDATE SET "
                "  finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "
                "  result_json=EXCLUDED.result_json, cost_json=EXCLUDED.cost_json"
            ),
            {
                "eid": uuid.UUID(eid),
                "pid": uuid.UUID(state["persona_id"]) if state.get("persona_id") else None,
                "goal": state.get("goal", ""),
                "start": _now(),
                "finish": _now(),
                "result": json.dumps(summary),
                "cost": json.dumps({
                    "tokens_in": summary["total_tokens_in"],
                    "tokens_out": summary["total_tokens_out"],
                    "cost_usd": summary["estimated_cost_usd"],
                }),
            },
        )

        # Save newsletter articles
        for nl in state.get("newsletter_articles", []):
            await db.execute(
                text(
                    "INSERT INTO newsletter_articles "
                    "  (id, execution_id, article_id, title, summary, body, "
                    "   product_refs, tags, source_url, img_url, media_refs, status, created_at) "
                    "VALUES (:id, :eid, :aid, :title, :summary, :body, "
                    "        CAST(:refs AS jsonb), :tags, :url, :img_url, CAST(:media AS jsonb), "
                    "        'draft', :now) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": uuid.UUID(nl["id"]),
                    "eid": uuid.UUID(eid),
                    "aid": uuid.UUID(nl["article_id"]) if nl.get("article_id") else None,
                    "title": nl["title"],
                    "summary": nl.get("summary", ""),
                    "body": nl.get("body", ""),
                    "refs": json.dumps(nl.get("product_refs", [])),
                    "tags": nl.get("tags", []),
                    "url": nl.get("source_url", ""),
                    "img_url": nl.get("img_url"),
                    "media": json.dumps(nl.get("media_refs") or {}),
                    "now": _now(),
                },
            )

        await db.commit()

    logger.info(_SEP)
    logger.info("CURATION COMPLETE  |  execution_id=%s", eid)
    logger.info(
        "  scraped=%d  extracted=%d  newsletters=%d  cost=$%.6f",
        summary["sources_scraped"],
        summary["articles_extracted"],
        summary["newsletter_articles_created"],
        summary["estimated_cost_usd"],
    )
    return {"summary": summary}


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_curation_graph() -> Any:
    g: StateGraph = StateGraph(CurationState)
    g.add_node("load_config", load_config)
    g.add_node("scrape_sources", scrape_sources)
    g.add_node("extract_content", extract_content)
    g.add_node("match_products", match_products)
    g.add_node("generate_newsletter", generate_newsletter)
    g.add_node("save_results", save_results)

    g.set_entry_point("load_config")
    g.add_edge("load_config", "scrape_sources")
    g.add_edge("scrape_sources", "extract_content")
    g.add_edge("extract_content", "match_products")
    g.add_edge("match_products", "generate_newsletter")
    g.add_edge("generate_newsletter", "save_results")
    g.add_edge("save_results", END)
    return g.compile()


_graph = build_curation_graph()


# ── Public entrypoint ──────────────────────────────────────────────────────────

async def run_curation_graph(
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    goal: str = "",
    effective_policy: dict | None = None,
    persona_id: uuid.UUID | None = None,
    persona: dict | None = None,
    persona_summary: str | None = None,
) -> dict:
    """Run the full content curation pipeline for one tenant."""
    logger.info(_SEP)
    logger.info(
        "CURATION START  |  tenant=%s  execution=%s  goal=%.100s",
        tenant_id, execution_id, goal,
    )

    initial_state: CurationState = {
        "tenant_id": str(tenant_id),
        "execution_id": str(execution_id),
        "goal": goal,
        "effective_policy": effective_policy or {},
        "persona_id": str(persona_id) if persona_id else None,
        "persona": persona,
        "persona_summary": persona_summary,
        "sources": [],
        "products": [],
        "tag_taxonomy": [],
        "scraped_pages": [],
        "applied_scraping_configs": [],
        "sources_scraped": 0,
        "pages_fetched": 0,
        "articles": [],
        "articles_extracted": 0,
        "article_products": [],
        "products_matched": 0,
        "newsletter_articles": [],
        "newsletter_count": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "estimated_cost_usd": 0.0,
        "summary": {},
        "error": None,
    }
    with langfuse_trace(
        tenant_id=tenant_id,
        execution_id=execution_id,
        service="content_curator",
        skill_id="content_curation",
    ) as lf_cfg:
        final_state: CurationState = await _graph.ainvoke(initial_state, config=lf_cfg)
    return final_state.get("summary", {})
